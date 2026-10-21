"""
Twilio and telephony webhook and status services.
Separated from call_routes.py to enforce single responsibility and code cleanliness.
"""
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, func
from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.core.config import settings
from app.models.call_log import CallLog
from app.models.campaign_contact import CampaignContact, CampaignContactStatus

logger = logging.getLogger(__name__)


async def validate_twilio_signature(request: Request) -> bool:
    """
    Validate that an incoming request genuinely came from Twilio.
    Reconstructs the public URL from X-Forwarded-Proto and X-Forwarded-Host headers.
    """
    if not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio auth token not configured — skipping signature validation")
        return True

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)

    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    forwarded_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
    if forwarded_proto and forwarded_host:
        url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
    else:
        url = str(request.url)

    form = await request.form()
    params = dict(form)
    signature = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, params, signature)


def generate_twiml(campaign_id: Optional[str] = None, contact_id: Optional[str] = None) -> str:
    """
    Generate TwiML XML instructions for incoming/connected calls.
    Bridges into LiveKit SIP trunk with duration and ringing guards if configured,
    or falls back to static prompt.
    """
    response = VoiceResponse()

    if settings.LIVEKIT_SIP_DOMAIN:
        room_parts = ["call"]
        if campaign_id:
            room_parts.append(campaign_id)
        if contact_id:
            room_parts.append(contact_id)
        room_name = "-".join(room_parts)

        sip_domain = settings.LIVEKIT_SIP_DOMAIN.removeprefix("sip:")
        sip_uri = f"sip:{room_name}@{sip_domain}"

        logger.info(
            f"TwiML: dialling LiveKit SIP {sip_uri} "
            f"(campaign={campaign_id}, contact={contact_id})"
        )

        recording_callback = f"{settings.BASE_URL}/call/twilio/recording-callback"
        dial = response.dial(
            timeout=120,
            time_limit=7200,
            record="record-from-answer-dual",
            recording_status_callback=recording_callback,
            recording_status_callback_event="completed",
            recording_status_callback_method="POST",
        )
        dial.sip(sip_uri)
    else:
        logger.warning(
            "LIVEKIT_SIP_DOMAIN not configured — playing static greeting. "
            "Set LIVEKIT_SIP_DOMAIN in .env to enable the AI agent."
        )
        if campaign_id:
            greeting = "Hello, you are being connected to our AI assistant."
        else:
            greeting = "Hello, this is an AI assistant calling on behalf of our team."

        response.say(greeting, voice="Polly.Joanna")
        response.pause(length=1)
        response.say("Thank you for your time. Goodbye.", voice="Polly.Joanna")

    return str(response)


async def handle_status_callback(form_data: dict, db: AsyncSession) -> dict:
    """
    Handle Twilio call status callbacks.
    Updates CallLog status and CampaignContact resolution.
    Publishes resolution event via Redis Pub/Sub for immediate campaign worker wakeup.
    """
    call_sid = form_data.get("CallSid", "")
    call_status = form_data.get("CallStatus", "")
    call_duration = form_data.get("CallDuration", "0")
    to_number = form_data.get("To", "")

    logger.info(
        f"Twilio status callback: SID={call_sid}, "
        f"Status={call_status}, Duration={call_duration}s, To={to_number}"
    )

    terminal_status_map = {
        "completed": "completed",
        "busy": "failed",
        "failed": "failed",
        "no-answer": "failed",
        "canceled": "failed",
    }

    if call_status in terminal_status_map and call_sid:
        internal_status = terminal_status_map[call_status]

        try:
            duration_seconds = int(call_duration) if call_duration else 0
        except ValueError:
            duration_seconds = 0

        # 1. Update the CallLog row
        log_result = await db.execute(
            update(CallLog)
            .where(CallLog.call_sid == call_sid)
            .values(status=internal_status, duration=duration_seconds)
            .returning(CallLog.contact_id, CallLog.campaign_id)
        )
        await db.commit()

        row = log_result.first()
        if row:
            logger.info(f"Call {call_sid} final status: {call_status} → {internal_status}")
            contact_id, campaign_id = row
            if contact_id and campaign_id:
                contact_status = (
                    CampaignContactStatus.COMPLETED
                    if internal_status == "completed"
                    else CampaignContactStatus.FAILED
                )
                await db.execute(
                    update(CampaignContact)
                    .where(
                        CampaignContact.campaign_id == campaign_id,
                        CampaignContact.contact_id == contact_id,
                    )
                    .values(call_status=contact_status)
                )
                await db.commit()
                logger.info(
                    f"CampaignContact {contact_id} → {contact_status.value} "
                    f"(campaign {campaign_id})"
                )

                # Event-driven resolution via Redis Pub/Sub
                try:
                    from app.services.campaign_executor import _notify_contact_resolution
                    await _notify_contact_resolution(campaign_id, contact_id, contact_status.value)
                except Exception as pub_err:
                    logger.debug(f"Could not publish resolution to Redis: {pub_err}")
        else:
            logger.warning(f"Status callback for unknown CallSid {call_sid} — no CallLog row updated")

    return {"status": "ok"}


async def handle_recording_callback(form_data: dict, db: AsyncSession) -> dict:
    """
    Handle Twilio recording callback to associate recording URL with CallLog.
    """
    call_sid = form_data.get("CallSid", "")
    recording_url = form_data.get("RecordingUrl", "")
    recording_status = form_data.get("RecordingStatus", "")
    recording_duration = form_data.get("RecordingDuration", "0")

    logger.info(
        f"Twilio recording callback: CallSid={call_sid}, "
        f"Status={recording_status}, Duration={recording_duration}s"
    )

    if recording_status != "completed" or not call_sid or not recording_url:
        return {"status": "ok"}

    try:
        rec_duration = int(recording_duration) if recording_duration else 0
    except ValueError:
        rec_duration = 0

    result = await db.execute(
        update(CallLog)
        .where(CallLog.call_sid == call_sid)
        .values(
            recording_url=recording_url,
            duration=func.coalesce(func.nullif(CallLog.duration, 0), rec_duration),
        )
        .returning(CallLog.id)
    )
    await db.commit()

    if result.first():
        logger.info(f"Recording saved for CallSid {call_sid}")
    else:
        logger.warning(f"Recording callback for unknown CallSid {call_sid} — no CallLog updated")

    return {"status": "ok"}


def check_twilio_status() -> dict:
    """
    Check whether Twilio credentials are configured and valid.
    """
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    phone = settings.TWILIO_PHONE_NUMBER

    if not sid or not token:
        return {
            "status": "missing",
            "message": "Twilio credentials are not configured.",
            "phone_number": phone or None,
        }

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        account = client.api.accounts(sid).fetch()
        return {
            "status": "connected",
            "message": "Twilio is connected and working.",
            "account_name": account.friendly_name,
            "account_status": account.status,
            "phone_number": phone or "Not configured",
            "account_sid_preview": f"{sid[:8]}...{sid[-4:]}",
        }
    except Exception as e:
        error_msg = str(e)
        if "authenticate" in error_msg.lower() or "20003" in error_msg:
            hint = "Your Account SID or Auth Token is incorrect."
        elif "not found" in error_msg.lower():
            hint = "The Account SID doesn't match any Twilio account."
        else:
            hint = "Unexpected error verifying Twilio credentials."
        return {
            "status": "invalid",
            "message": hint,
            "error": error_msg,
            "account_sid_preview": f"{sid[:8]}...{sid[-4:]}",
        }
