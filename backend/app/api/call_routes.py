import time
# Fix #32: All imports moved to the top of the file (previously split mid-file)
import json
import logging
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, update
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse
from livekit.api import AccessToken, VideoGrants, SIPParticipantInfo

from app.services.call_service import start_call
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business
from app.models.call_log import CallLog
from app.models.contact import Contact
from app.models.business import Business
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact, CampaignContactStatus
from app.schemas.call_schema import CallLogCreate, CallLogOut, AnalyticsOut
from app.core.config import settings
from app.core.rate_limit import limiter

router = APIRouter()

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CAMPAIGN EXECUTION (Start / Pause / Stop / Progress)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.campaign_executor import (
    start_campaign_execution,
    pause_campaign_execution,
    stop_campaign_execution,
    get_campaign_progress,
)

@router.post("/campaign/{campaign_id}/start")
@limiter.limit("30/minute")
async def start_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Start executing a campaign — calls all pending contacts sequentially."""
    business = await get_user_business(db, user_id)

    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id, Campaign.business_id == business.id)
    )).scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status in [CampaignStatus.ACTIVE]:
        raise HTTPException(status_code=400, detail="Campaign is already running")

    contact_count = (await db.execute(
        select(func.count(CampaignContact.id)).where(CampaignContact.campaign_id == campaign_id)
    )).scalar() or 0

    if contact_count == 0:
        raise HTTPException(status_code=400, detail="No contacts assigned to this campaign")

    result = await start_campaign_execution(campaign_id, business.id)
    return {"message": f"Campaign '{campaign.name}' started with {contact_count} contacts", **result}


@router.post("/campaign/{campaign_id}/pause")
@limiter.limit("30/minute")
async def pause_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Pause a running campaign. Pending contacts will not be called until resumed."""
    business = await get_user_business(db, user_id)

    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id, Campaign.business_id == business.id)
    )).scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    result = await pause_campaign_execution(campaign_id)
    return {"message": f"Campaign '{campaign.name}' paused", **result}


@router.post("/campaign/{campaign_id}/stop")
@limiter.limit("30/minute")
async def stop_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Stop a campaign permanently. Sets status to CANCELLED."""
    business = await get_user_business(db, user_id)

    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id, Campaign.business_id == business.id)
    )).scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    result = await stop_campaign_execution(campaign_id)
    return {"message": f"Campaign '{campaign.name}' stopped", **result}


@router.get("/campaign/{campaign_id}/progress")
async def campaign_progress(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Get real-time progress of a campaign (pending/calling/completed/failed counts)."""
    business = await get_user_business(db, user_id)

    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id, Campaign.business_id == business.id)
    )).scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    progress = await get_campaign_progress(db, campaign_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return progress


# ── Campaign Details analytics ────────────────────────────────────────
# Real per-campaign business intelligence computed from call logs + the
# campaign's contacts, in the exact shape the Campaign Details page needs.

_OUTCOME_TO_RECENT = {
    "confirmed": "Appointment",
    "rescheduled": "Callback",
    "callback_requested": "Callback",
    "not_interested": "Not Interested",
    "do_not_call": "Not Interested",
    "wrong_person": "No Answer",
    "incomplete": "Voicemail",
    "other": "Qualified",
}

_CAMPAIGN_STATUS_TO_RUN = {
    "active": "running",
    "paused": "paused",
    "scheduled": "scheduled",
    "completed": "completed",
    "draft": "draft",
    "cancelled": "cancelled",
}


def _fmt_duration(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 60}m {s % 60:02d}s"


def _relative_time(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


@router.get("/campaign/{campaign_id}/analytics")
async def campaign_analytics(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Real per-campaign analytics for the Campaign Details page: KPIs,
    funnel, progress, recent calls and timeline computed from call logs."""
    business = await get_user_business(db, user_id)

    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id, Campaign.business_id == business.id)
    )).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    progress = await get_campaign_progress(db, campaign_id) or {}
    total_contacts = progress.get("total_contacts", 0)
    completed = progress.get("completed", 0)
    failed = progress.get("failed", 0)
    calling = progress.get("calling", 0)
    pending = progress.get("pending", 0)

    logs = (await db.execute(
        select(CallLog)
        .where(CallLog.campaign_id == campaign_id)
        .order_by(CallLog.created_at.desc())
    )).scalars().all()

    outcome_counts: dict[str, int] = {}
    for log in logs:
        if log.outcome:
            outcome_counts[log.outcome] = outcome_counts.get(log.outcome, 0) + 1
    appointments = outcome_counts.get("confirmed", 0)
    interested = (
        appointments
        + outcome_counts.get("rescheduled", 0)
        + outcome_counts.get("callback_requested", 0)
    )

    contacts = (await db.execute(
        select(Contact)
        .join(CampaignContact, CampaignContact.contact_id == Contact.id)
        .where(CampaignContact.campaign_id == campaign_id)
    )).scalars().all()
    qualified = sum(1 for c in contacts if (c.lead_score or 0) >= 75)
    converted = sum(1 for c in contacts if c.pipeline_stage == "won")
    contacts_by_id = {str(c.id): c for c in contacts}

    answered = completed
    conversion_rate = round(qualified / answered * 100, 1) if answered > 0 else 0.0
    success_rate = round(completed / (completed + failed) * 100) if (completed + failed) > 0 else 0
    est_pipeline = appointments * 4000  # estimate — no revenue tracking yet

    kpis = [
        {"id": "calls-completed", "title": "Calls Completed", "value": completed, "subLabel": "Total answered", "changeType": "positive", "icon": "calls-completed"},
        {"id": "calls-in-progress", "title": "Calls In Progress", "value": calling, "subLabel": "Currently active", "changeType": "neutral", "icon": "in-progress"},
        {"id": "qualified-leads", "title": "Qualified Leads", "value": qualified, "subLabel": "Lead Score >75", "changeType": "neutral", "icon": "qualified"},
        {"id": "appointments-booked", "title": "Appointments Booked", "value": appointments, "subLabel": "Confirmed", "changeType": "neutral", "icon": "appointments"},
        {"id": "conversion-rate", "title": "Conversion Rate", "value": f"{conversion_rate}%", "subLabel": "Answered → Qualified", "changeType": "neutral", "icon": "conversion"},
        {"id": "estimated-pipeline", "title": "Estimated Pipeline", "value": f"${est_pipeline:,}", "subLabel": "Generated opportunities", "changeType": "positive", "icon": "pipeline", "isHero": True},
    ]

    funnel = [
        {"id": "uploaded", "label": "Uploaded", "value": total_contacts},
        {"id": "answered", "label": "Answered", "value": answered},
        {"id": "interested", "label": "Interested", "value": interested},
        {"id": "qualified", "label": "Qualified", "value": qualified},
        {"id": "appointments", "label": "Appointments", "value": appointments},
        {"id": "converted", "label": "Converted", "value": converted},
    ]

    recent_calls = []
    for log in logs[:8]:
        c = contacts_by_id.get(str(log.contact_id))
        insights = (c.ai_insights or {}) if c else {}
        recent_calls.append({
            "id": str(log.id),
            "customer": c.name if c else "Unknown",
            "duration": _fmt_duration(log.duration),
            "outcome": _OUTCOME_TO_RECENT.get(log.outcome, "Qualified") if log.outcome else "Voicemail",
            "leadScore": (c.lead_score if c else 0) or 0,
            "sentiment": insights.get("sentiment", "Neutral"),
            "nextAction": log.follow_up or "Follow-up",
        })

    timeline = []
    for log in logs[:6]:
        if log.outcome == "confirmed":
            title, ttype = "Appointment booked", "success"
        elif log.outcome in ("callback_requested", "rescheduled"):
            title, ttype = "Customer requested callback", "warning"
        elif log.status == "completed":
            title, ttype = "Call completed", "success"
        elif log.status == "failed":
            title, ttype = "Call failed", "warning"
        else:
            title, ttype = "Call started", "info"
        timeline.append({"id": str(log.id), "type": ttype, "title": title, "time": _relative_time(log.created_at)})

    status_val = campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status)

    return {
        "header": {
            "name": campaign.name,
            "status": _CAMPAIGN_STATUS_TO_RUN.get(status_val, "draft"),
            "createdAt": campaign.created_at.isoformat() if campaign.created_at else None,
            "updatedAt": campaign.updated_at.isoformat() if campaign.updated_at else None,
        },
        "kpis": kpis,
        "funnel": funnel,
        "progress": {
            "completed": completed,
            "total": total_contacts,
            "etaLabel": "In progress" if progress.get("is_running") else "Not running",
            "retryQueue": pending,
            "failedCalls": failed,
            "successRate": success_rate,
        },
        "recentCalls": recent_calls,
        "timeline": timeline,
        "settings": {
            "voice": campaign.ai_voice or "—",
            "language": campaign.language or "en",
            "retries": f"{campaign.max_retries} attempts" if campaign.max_retries is not None else "—",
            "knowledgeBase": "—",
            "promptVersion": "v1",
            "launchSchedule": campaign.scheduled_at.isoformat() if campaign.scheduled_at else "Manual",
            "timezone": "—",
        },
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TWILIO WEBHOOKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _validate_twilio_signature(request: Request) -> bool:
    """
    Validate that an incoming request genuinely came from Twilio.

    Bug #1 fix: When sitting behind a reverse proxy (ngrok / nginx), FastAPI
    sees the internal localhost URL, but Twilio signs the request with the
    public-facing URL.  We reconstruct the public URL from X-Forwarded-Proto
    and X-Forwarded-Host headers injected by the proxy.  Make sure you run
    uvicorn with --proxy-headers so FastAPI trusts those headers.
    """
    if not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio auth token not configured — skipping signature validation")
        return True  # Allow in dev mode when Twilio isn't configured

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)

    # Reconstruct the public URL Twilio actually called.
    # X-Forwarded-Proto / X-Forwarded-Host are set by ngrok and most proxies.
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    forwarded_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
    if forwarded_proto and forwarded_host:
        # Rebuild: scheme://host/path?query
        url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
    else:
        url = str(request.url)

    form = await request.form()
    params = dict(form)
    signature = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, params, signature)


@router.post("/twilio/twiml")
async def twilio_twiml(
    request: Request,
    campaign_id: Optional[str] = None,
    contact_id: Optional[str] = None,
):
    """
    Endpoint for Twilio to fetch TwiML instructions when a call connects.

    Bug #3 fix: When LIVEKIT_SIP_DOMAIN is configured this bridges the Twilio
    call into a per-call LiveKit room via SIP so the AI agent can handle the
    conversation.  The room name encodes campaign + contact so the AI agent
    worker knows which campaign/contact context to load.

    Falls back to a static recorded greeting when SIP is not yet configured
    (useful for smoke-testing Twilio connectivity without a LiveKit SIP trunk).
    """
    if not await _validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on TwiML request")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    response = VoiceResponse()

    if settings.LIVEKIT_SIP_DOMAIN:
        # ── SIP bridge path (production) ──────────────────────────────────
        # Build a unique room name for this call so the AI agent joins the
        # right LiveKit room.  Pattern: call-<campaign_id>-<contact_id>
        room_parts = ["call"]
        if campaign_id:
            room_parts.append(campaign_id)
        if contact_id:
            room_parts.append(contact_id)
        room_name = "-".join(room_parts)

        # Encode campaign/contact as SIP URI user-part so the LiveKit
        # dispatch-rule (or the agent worker) can read them from the room name.
        # SIP address: sip:<room>@<livekit-sip-domain>
        # LIVEKIT_SIP_DOMAIN is a bare domain (LiveKit Console → SIP → Trunks),
        # but strip an accidental "sip:" prefix so a misconfigured .env doesn't
        # produce a double-scheme URI like "sip:room@sip:domain".
        sip_domain = settings.LIVEKIT_SIP_DOMAIN.removeprefix("sip:")
        sip_uri = f"sip:{room_name}@{sip_domain}"

        logger.info(
            f"TwiML: dialling LiveKit SIP {sip_uri} "
            f"(campaign={campaign_id}, contact={contact_id})"
        )

        # Record the bridged conversation (both legs) so it can be played back
        # in Call Details. Twilio POSTs the finished recording to the callback
        # below, which stores the media URL against this call's CallLog row.
        recording_callback = f"{settings.BASE_URL}/call/twilio/recording-callback"
        dial = response.dial(
            record="record-from-answer-dual",
            recording_status_callback=recording_callback,
            recording_status_callback_event="completed",
            recording_status_callback_method="POST",
        )
        dial.sip(sip_uri)

    else:
        # ── Fallback: static greeting (no SIP trunk configured yet) ───────
        # This lets you confirm Twilio → ngrok → backend is working even
        # before LiveKit SIP is set up.
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

    return Response(content=str(response), media_type="text/xml")


@router.post("/twilio/status-callback")
async def twilio_status_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives call status updates from Twilio.
    Twilio sends: CallSid, CallStatus, CallDuration, To, From, etc.

    Bug #2 fix: This is now the ONLY place that sets a CampaignContact to
    COMPLETED or FAILED for real Twilio calls.  The campaign executor no longer
    sets COMPLETED on "initiated" — it waits here.
    """
    if not await _validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on status callback")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    try:
        form = await request.form()
        call_sid = form.get("CallSid", "")
        call_status = form.get("CallStatus", "")
        call_duration = form.get("CallDuration", "0")
        to_number = form.get("To", "")

        logger.info(
            f"Twilio status callback: SID={call_sid}, "
            f"Status={call_status}, Duration={call_duration}s, To={to_number}"
        )

        # Only act on terminal statuses (Twilio also sends 'initiated'/'ringing'
        # events which we subscribed to, but those are not final).
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

                # 2. Bug #2 fix: Update CampaignContact so the campaign executor
                #    polling loop can see the contact has left CALLING state.
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
            else:
                logger.warning(
                    f"Status callback for unknown CallSid {call_sid} — no CallLog row updated"
                )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing Twilio status callback: {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/twilio/recording-callback")
async def twilio_recording_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives the finished-recording notification from Twilio (fired by the
    `recordingStatusCallback` set on the <Dial> verb in the TwiML above).

    Twilio sends: CallSid, RecordingSid, RecordingUrl, RecordingStatus,
    RecordingDuration. We store RecordingUrl against the CallLog row matching
    CallSid; the audio itself is streamed to the frontend later via the
    authenticated /call/logs/{id}/recording proxy (Twilio media requires auth).
    """
    if not await _validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on recording callback")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    try:
        form = await request.form()
        call_sid = form.get("CallSid", "")
        recording_url = form.get("RecordingUrl", "")
        recording_status = form.get("RecordingStatus", "")
        recording_duration = form.get("RecordingDuration", "0")

        logger.info(
            f"Twilio recording callback: CallSid={call_sid}, "
            f"Status={recording_status}, Duration={recording_duration}s"
        )

        # Only persist a completed recording with a usable media URL.
        if recording_status != "completed" or not call_sid or not recording_url:
            return {"status": "ok"}

        try:
            rec_duration = int(recording_duration) if recording_duration else 0
        except ValueError:
            rec_duration = 0

        # Store the media URL. Also backfill duration from the recording length
        # when the status callback hasn't set one yet (COALESCE keeps any
        # existing non-zero value).
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
            logger.warning(
                f"Recording callback for unknown CallSid {call_sid} — no CallLog updated"
            )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing Twilio recording callback: {e}")
        return {"status": "error", "detail": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SINGLE CALL + TWILIO HEALTH CHECK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/start/{contact_id}")
@limiter.limit("30/minute")
async def start_call_route(
    request: Request,
    contact_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Start a single outbound call to a contact."""
    business = await get_user_business(db, user_id)
    contact_stmt = select(Contact).filter(
        Contact.id == UUID(contact_id),
        Contact.business_id == business.id
    )
    result = await db.execute(contact_stmt)
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found or not authorized")

    return await start_call(db, contact_id)


@router.get("/twilio/status")
async def twilio_status(user_id: str = Depends(get_current_user)):
    """Check whether Twilio credentials are configured and valid."""
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALL LOGS & ANALYTICS (tenant-scoped)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/log", response_model=CallLogOut)
async def create_call_log(
    log_data: CallLogCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    contact_stmt = select(Contact).filter(
        Contact.id == log_data.contact_id,
        Contact.business_id == business.id
    )
    result = await db.execute(contact_stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Contact does not belong to your business")

    new_log = CallLog(
        contact_id=log_data.contact_id,
        business_id=business.id,
        status=log_data.status,
        transcript=log_data.transcript,
        duration=log_data.duration,
    )
    db.add(new_log)
    await db.commit()
    await db.refresh(new_log)
    return new_log


@router.get("/logs", response_model=List[CallLogOut])
async def get_call_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    stmt = (
        select(CallLog)
        .filter(CallLog.business_id == business.id)
        .order_by(CallLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ── Call Details ──────────────────────────────────────────────────────
_CALL_STATUS_MAP = {"completed": "completed", "failed": "failed", "started": "in-progress"}


def _sentiment_tone(s) -> str:
    s = (s or "").lower()
    if s == "positive":
        return "positive"
    if s == "negative":
        return "negative"
    return "neutral"


@router.get("/logs/{log_id}")
async def get_call_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Real Call Details for a single call — header, transcript, summary, and
    the KPIs/intelligence derivable from stored data. AI-enriched sections
    (business analysis, coaching, AI improvement) come from a later step."""
    business = await get_user_business(db, user_id)

    log = (await db.execute(
        select(CallLog).filter(CallLog.id == log_id, CallLog.business_id == business.id)
    )).scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Call not found")

    contact = None
    if log.contact_id:
        contact = (await db.execute(
            select(Contact).filter(Contact.id == log.contact_id)
        )).scalar_one_or_none()
    campaign = None
    if log.campaign_id:
        campaign = (await db.execute(
            select(Campaign).filter(Campaign.id == log.campaign_id)
        )).scalar_one_or_none()

    insights = (contact.ai_insights or {}) if contact else {}
    lead_score = contact.lead_score if contact else None
    sentiment = insights.get("sentiment", "Neutral")
    buying_intent = insights.get("buying_intent", "Medium")
    decision_maker = insights.get("decision_maker", "Unknown")
    conv_prob = insights.get("conversion_probability")

    if log.outcome == "confirmed":
        appointment = "Booked"
    elif log.outcome == "rescheduled":
        appointment = "Rescheduled"
    else:
        appointment = "Not booked"
    next_action = log.follow_up or "—"

    status = _CALL_STATUS_MAP.get(log.status, "completed")
    if log.outcome == "wrong_person":
        status = "no-answer"
    elif log.outcome == "incomplete" and log.status != "completed":
        status = "voicemail"

    # Summary → bullet points
    summary = None
    if log.summary:
        s = log.summary.strip()
        pts = [seg.strip() for seg in s.split(". ") if seg.strip()]
        summary = [seg if seg.endswith((".", "!", "?")) else seg + "." for seg in pts] or [s]

    # Transcript → chat-style segments (timestamps distributed across duration)
    transcript = None
    if log.transcript:
        lines = [ln.strip() for ln in log.transcript.splitlines() if ln.strip()]
        dur = int(log.duration or 0)
        n = len(lines)
        segs = []
        for i, ln in enumerate(lines):
            low = ln.lower()
            if low.startswith(("agent:", "ai:", "assistant:")):
                speaker, text = "ai", ln.split(":", 1)[1].strip()
            elif low.startswith(("user:", "customer:", "caller:")):
                speaker, text = "customer", ln.split(":", 1)[1].strip()
            else:
                speaker, text = "ai", ln
            ts = round(i * dur / n) if n > 0 else 0
            segs.append({"id": f"s{i}", "speaker": speaker, "timestamp": ts, "text": text, "tags": []})
        transcript = segs or None

    # When Twilio has stored a recording, point the frontend at our authenticated
    # proxy (it fetches the blob with the user's token — Twilio media isn't public).
    # Otherwise fall back to the duration-only shape that drives the simulated
    # waveform, or None when there's nothing at all.
    if log.recording_url:
        recording = {"url": f"/call/logs/{log.id}/recording", "durationSeconds": int(log.duration or 0)}
    elif (log.duration or 0) > 0:
        recording = {"url": None, "durationSeconds": int(log.duration or 0)}
    else:
        recording = None

    kpis = [
        {"id": "lead-score", "label": "Lead Score", "value": f"{lead_score} / 100" if lead_score is not None else "—", "hint": "Contact score", "tone": "accent", "icon": "lead-score"},
        {"id": "buying-intent", "label": "Buying Intent", "value": buying_intent, "tone": "positive" if buying_intent == "High" else "neutral", "icon": "buying-intent"},
        {"id": "sentiment", "label": "Sentiment", "value": sentiment, "tone": _sentiment_tone(sentiment), "icon": "sentiment"},
        {"id": "decision-maker", "label": "Decision Maker", "value": decision_maker, "tone": "neutral", "icon": "decision-maker"},
        {"id": "appointment", "label": "Appointment", "value": appointment, "tone": "positive" if appointment == "Booked" else "neutral", "icon": "appointment"},
        {"id": "next-action", "label": "Next Action", "value": next_action[:40], "tone": "neutral", "icon": "next-action"},
    ]

    intelligence = [
        {"id": "i-lead", "label": "Lead Score", "value": str(lead_score) if lead_score is not None else "—", "tone": "accent", "progress": lead_score},
        {"id": "i-intent", "label": "Buying Intent", "value": buying_intent, "tone": "positive" if buying_intent == "High" else "neutral"},
        {"id": "i-budget", "label": "Estimated Budget", "value": insights.get("estimated_budget", "—"), "tone": "neutral"},
        {"id": "i-dm", "label": "Decision Maker", "value": decision_maker, "tone": "neutral"},
        {"id": "i-timeline", "label": "Expected Closing", "value": insights.get("closing_timeline", "—"), "tone": "neutral"},
        {"id": "i-risk", "label": "Risk Level", "value": insights.get("risk_level", "—"), "tone": "neutral"},
        {"id": "i-quality", "label": "Conversation Quality", "value": f"{conv_prob}%" if conv_prob is not None else "—", "tone": "accent", "progress": conv_prob},
    ]

    return {
        "header": {
            "customerName": contact.name if contact else "Unknown Contact",
            "company": (contact.company if contact else "") or "—",
            "phone": (contact.phone_number if contact else "") or "—",
            "campaign": campaign.name if campaign else "—",
            "status": status,
            "durationLabel": _fmt_duration(log.duration),
            "date": log.created_at.isoformat() if log.created_at else None,
        },
        "kpis": kpis,
        "intelligence": intelligence,
        "recording": recording,
        "summary": summary,
        "transcript": transcript,
    }


@router.get("/logs/{log_id}/recording")
async def get_call_recording(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Authenticated proxy for a call's Twilio recording audio.

    Twilio recording media requires account credentials, so the frontend can't
    load it directly with an <audio> tag (which can't send the user's bearer
    token either). Instead the browser fetches this endpoint with auth, and we
    stream the audio from Twilio — scoped to the caller's business — back as MP3.
    """
    business = await get_user_business(db, user_id)

    log = (await db.execute(
        select(CallLog).filter(CallLog.id == log_id, CallLog.business_id == business.id)
    )).scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Call not found")
    if not log.recording_url:
        raise HTTPException(status_code=404, detail="No recording for this call")

    # Twilio media URLs serve MP3 when you append ".mp3"; auth is HTTP Basic
    # with the account SID + auth token.
    media_url = log.recording_url
    if not media_url.endswith((".mp3", ".wav")):
        media_url = f"{media_url}.mp3"
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    # Call recordings are small MP3s (a few MB even for long calls), and the
    # frontend loads the result into a blob/object URL for playback, so a single
    # buffered fetch is simplest and gives full client-side seeking.
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.get(media_url, auth=auth)
    except httpx.HTTPError as e:
        logger.error(f"Twilio media fetch error for log {log_id}: {e}")
        raise HTTPException(status_code=502, detail="Recording unavailable upstream")

    if upstream.status_code != 200:
        logger.error(f"Twilio media fetch failed ({upstream.status_code}) for log {log_id}")
        raise HTTPException(status_code=502, detail="Recording unavailable upstream")

    return Response(
        content=upstream.content,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/analytics", response_model=AnalyticsOut)
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    start = time.perf_counter()

    db_start = time.perf_counter()

    business = await get_user_business(db, user_id)

    db_elapsed = (time.perf_counter() - db_start) * 1000
    print(f"GET_USER_BUSINESS_MS={db_elapsed:.2f}")

    biz_id = business.id




    
    total_calls = (await db.execute(
        select(func.count(CallLog.id)).where(CallLog.business_id == biz_id)
    )).scalar() or 0

    completed_calls = (await db.execute(
        select(func.count(CallLog.id)).where(
            CallLog.business_id == biz_id, CallLog.status == "completed"
        )
    )).scalar() or 0

    failed_calls = (await db.execute(
        select(func.count(CallLog.id)).where(
            CallLog.business_id == biz_id, CallLog.status == "failed"
        )
    )).scalar() or 0

    avg_duration = (await db.execute(
        select(func.avg(CallLog.duration)).where(CallLog.business_id == biz_id)
    )).scalar() or 0.0

    active_campaigns = (await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.business_id == biz_id,
            Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED])
        )
    )).scalar() or 0

    total_contacts = (await db.execute(
        select(func.count(Contact.id)).where(Contact.business_id == biz_id)
    )).scalar() or 0

    # Fix #7: Replace loading all CallLog ORM objects into memory for trend
    # calculation with a single SQL DATE_TRUNC + GROUP BY aggregate query.
    # Previously this fetched full rows (with transcript text!) for every log
    # in the last 7 days — catastrophic at scale. Now it's O(1) server memory.
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=6)

    # Build the date_trunc('day', ...) expression once and reuse the same
    # object in SELECT/GROUP BY/ORDER BY. Calling func.date_trunc("day", ...)
    # separately in each clause binds "day" as three independent parameters
    # ($1/$2/$3) — Postgres then can't prove the SELECTed expression is
    # functionally dependent on the GROUP BY expression (different params,
    # even though they'd carry the same value) and raises:
    # "column call_logs.created_at must appear in the GROUP BY clause".
    # Reusing one expression object makes SQLAlchemy emit the same bound
    # parameter everywhere, which Postgres can then verify as identical.
    day_bucket = func.date_trunc("day", CallLog.created_at)

    trend_rows = (await db.execute(
        select(
            func.to_char(day_bucket, "Mon DD").label("day"),
            func.count(CallLog.id).label("calls"),
        )
        .where(
            CallLog.business_id == biz_id,
            CallLog.created_at >= seven_days_ago,
        )
        .group_by(day_bucket)
        .order_by(day_bucket.asc())
    )).all()

    # Build the full 7-day scaffold so days with zero calls still appear
    trends_dict: dict[str, int] = {}
    for i in range(6, -1, -1):
        day_str = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%b %d")
        trends_dict[day_str] = 0
    for row in trend_rows:
        day_str = row.day.strip()
        if day_str in trends_dict:
            trends_dict[day_str] = row.calls

    result = AnalyticsOut(
        total_calls=total_calls,
        completed_calls=completed_calls,
        failed_calls=failed_calls,
        average_duration_seconds=float(avg_duration),
        active_campaigns=active_campaigns,
        total_contacts=total_contacts,
        call_trends=[{"date": k, "calls": v} for k, v in trends_dict.items()]
    )

    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"ANALYTICS_TOTAL_MS={elapsed_ms:.2f}")

    return result
    


