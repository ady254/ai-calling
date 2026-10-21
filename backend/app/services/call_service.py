import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from app.models.contact import Contact, ContactStatus
from app.models.call_log import CallLog
from app.providers import TelephonyProvider, get_telephony_provider
from app.services.twilio_service import make_outbound_call

logger = logging.getLogger(__name__)

async def start_call(
    db: AsyncSession,
    contact_id: str,
    campaign_id: str | None = None,
    telephony_provider: TelephonyProvider | None = None,
):
    """
    Initiate an outbound call via TelephonyProvider to the contact.
    Optionally pass campaign_id for context.
    """
    stmt = select(Contact).filter(Contact.id == UUID(contact_id))
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    if not contact.phone_number:
        raise HTTPException(status_code=400, detail="Contact does not have a phone number")

    if contact.status == ContactStatus.DO_NOT_CALL:
        raise HTTPException(status_code=403, detail="Contact is marked as do-not-call")

    logger.info(f"Starting outbound call to {contact.phone_number}")

    # make_outbound_call uses synchronous SDK calls under the hood; run
    # it in a thread so it doesn't block the event loop for the duration of
    # the HTTP round-trip.
    provider = telephony_provider or get_telephony_provider()
    call_result = await asyncio.to_thread(
        provider.make_call,
        to=contact.phone_number,
        campaign_id=campaign_id,
        contact_id=contact_id,
    )

    call_log = CallLog(
        contact_id=contact.id,
        campaign_id=UUID(campaign_id) if campaign_id else None,
        business_id=contact.business_id,
        status="started" if call_result.get("status") == "initiated" else call_result.get("status", "failed"),
        call_sid=call_result.get("sid"),
    )
    db.add(call_log)
    await db.commit()

    return {
        "status": call_result.get("status", "unknown"),
        "contact_id": contact_id,
        "phone_number": contact.phone_number,
        "twilio_response": call_result
    }