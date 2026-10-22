import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func

from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business
from app.models.call_log import CallLog
from app.models.contact import Contact
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact
from app.repositories import CampaignRepository, ContactRepository, CallLogRepository
from app.schemas.call_schema import CallLogCreate, CallLogOut, AnalyticsOut
from app.schemas.analytics_schema import CampaignAnalytics, CallDetailOut
from app.core.rate_limit import limiter
from app.services import analytics_service
from app.services.call_service import start_call, fetch_call_recording_bytes
from app.services.campaign_executor import (
    start_campaign_execution,
    pause_campaign_execution,
    stop_campaign_execution,
    get_campaign_progress,
)
from app.services.twilio_webhook_service import (
    validate_twilio_signature,
    generate_twiml,
    handle_status_callback,
    handle_recording_callback,
    check_twilio_status,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CAMPAIGN EXECUTION (Start / Pause / Stop / Progress / Analytics)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/campaign/{campaign_id}/start")
@limiter.limit("30/minute")
async def start_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Start executing a campaign — calls all pending contacts concurrently."""
    business = await get_user_business(db, user_id)
    repo = CampaignRepository(db)
    campaign = await repo.get_by_id(campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status in [CampaignStatus.ACTIVE]:
        raise HTTPException(status_code=400, detail="Campaign is already running")

    pending_ids = await repo.list_pending_contact_ids(campaign_id)
    contact_count = len(pending_ids)
    if contact_count == 0:
        # Fallback: count all contacts, not just pending
        status_counts = await repo.get_contact_status_counts(campaign_id)
        contact_count = sum(status_counts.values())
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
    user_id: str = Depends(get_current_user),
):
    """Pause a running campaign."""
    business = await get_user_business(db, user_id)
    campaign = await CampaignRepository(db).get_by_id(campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")

    result = await pause_campaign_execution(campaign_id)
    return {"message": f"Campaign '{campaign.name}' paused", **result}


@router.post("/campaign/{campaign_id}/stop")
@limiter.limit("30/minute")
async def stop_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Stop a campaign permanently. Sets status to CANCELLED."""
    business = await get_user_business(db, user_id)
    campaign = await CampaignRepository(db).get_by_id(campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")

    result = await stop_campaign_execution(campaign_id)
    return {"message": f"Campaign '{campaign.name}' stopped", **result}


@router.get("/campaign/{campaign_id}/progress")
async def campaign_progress(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Get real-time progress of a campaign."""
    business = await get_user_business(db, user_id)
    campaign = await CampaignRepository(db).get_by_id(campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")

    progress = await get_campaign_progress(db, campaign_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return progress


@router.get("/campaign/{campaign_id}/analytics", response_model=CampaignAnalytics)
async def campaign_analytics(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> CampaignAnalytics:
    """Per-campaign analytics computed via analytics service."""
    business = await get_user_business(db, user_id)
    return await analytics_service.get_campaign_analytics(db, campaign_id, business.id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TWILIO WEBHOOKS & HEALTH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/twilio/twiml")
async def twilio_twiml(
    request: Request,
    campaign_id: Optional[str] = None,
    contact_id: Optional[str] = None,
):
    """Endpoint for Twilio to fetch TwiML instructions when a call connects."""
    if not await validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on TwiML request")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    twiml_content = generate_twiml(campaign_id, contact_id)
    return Response(content=twiml_content, media_type="text/xml")


@router.post("/twilio/status-callback")
async def twilio_status_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Receives call status updates from Twilio."""
    if not await validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on status callback")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    try:
        form = await request.form()
        return await handle_status_callback(dict(form), db)
    except Exception as e:
        logger.error(f"Error processing Twilio status callback: {e}")
        return {"status": "error", "detail": str(e)}


@router.post("/twilio/recording-callback")
async def twilio_recording_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Receives finished-recording notifications from Twilio."""
    if not await validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on recording callback")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    try:
        form = await request.form()
        return await handle_recording_callback(dict(form), db)
    except Exception as e:
        logger.error(f"Error processing Twilio recording callback: {e}")
        return {"status": "error", "detail": str(e)}


@router.get("/twilio/status")
async def twilio_status(user_id: str = Depends(get_current_user)):
    """Check whether Twilio credentials are configured and valid."""
    return check_twilio_status()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SINGLE CALL & LOG MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/start/{contact_id}")
@limiter.limit("30/minute")
async def start_call_route(
    request: Request,
    contact_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Start a single outbound call to a contact."""
    business = await get_user_business(db, user_id)
    contact = await ContactRepository(db).get_by_id(UUID(contact_id))
    if not contact or contact.business_id != business.id:
        raise HTTPException(status_code=404, detail="Contact not found or not authorized")

    return await start_call(db, contact_id)


@router.post("/log", response_model=CallLogOut)
async def create_call_log(
    log_data: CallLogCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    business = await get_user_business(db, user_id)
    contact = await ContactRepository(db).get_by_id(log_data.contact_id)
    if not contact or contact.business_id != business.id:
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
    user_id: str = Depends(get_current_user),
):
    business = await get_user_business(db, user_id)
    logs = await CallLogRepository(db).list_by_business(business.id, skip=skip, limit=limit)
    return logs


@router.get("/logs/{log_id}", response_model=CallDetailOut)
async def get_call_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> CallDetailOut:
    """Real Call Details for a single call via analytics service."""
    business = await get_user_business(db, user_id)
    return await analytics_service.get_call_detail(db, log_id, business.id)


@router.get("/logs/{log_id}/recording")
async def get_call_recording(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Authenticated proxy for a call's Twilio recording audio."""
    business = await get_user_business(db, user_id)
    content = await fetch_call_recording_bytes(db, log_id, business.id)
    return Response(
        content=content,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/analytics", response_model=AnalyticsOut)
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> AnalyticsOut:
    business = await get_user_business(db, user_id)
    return await analytics_service.get_business_analytics(db, business.id)
