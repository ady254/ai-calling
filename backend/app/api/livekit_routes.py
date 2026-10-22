import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.livekit_service import create_room_token
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business
from app.dependencies.database import get_db
from app.core.config import settings
from app.core.rate_limit import limiter
from app.repositories import CampaignRepository, ContactRepository

logger = logging.getLogger(__name__)
router = APIRouter()


class RoomRequest(BaseModel):
    room_name: str = "test-room"


@router.get("/token")
@limiter.limit("20/minute")
async def get_token(
    request: Request,
    room_name: str,
    campaign_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """
    Generate a LiveKit room token for the authenticated user.

    SECURITY: room_name is caller-supplied, and campaign_id/contact_id are
    used only to build participant metadata — none of that was previously
    checked against the caller's own tenant. That meant any authenticated
    user could mint a token for *any* room_name (including one belonging to
    another business's live call) and join it to listen in or speak.
    Two fixes:
      1. If campaign_id/contact_id are supplied, verify they belong to the
         caller's business before embedding them in the token metadata.
      2. Namespace the actual LiveKit room by business_id server-side, so a
         user can never join a room outside their own tenant's namespace
         regardless of what room_name string they pass.
    """
    business = await get_user_business(db, user_id)

    if campaign_id:
        campaign = await CampaignRepository(db).get_by_id(campaign_id)
        if not campaign or campaign.business_id != business.id:
            raise HTTPException(status_code=403, detail="Campaign not found or not authorized")

    if contact_id:
        contact = await ContactRepository(db).get_by_id(contact_id)
        if not contact or contact.business_id != business.id:
            raise HTTPException(status_code=403, detail="Contact not found or not authorized")

    metadata = {}
    if campaign_id:
        metadata["campaign_id"] = campaign_id
    if contact_id:
        metadata["contact_id"] = contact_id

    # Namespace the room per-business so callers can never collide with (or
    # deliberately target) another tenant's room, no matter what room_name
    # string is supplied.
    scoped_room_name = f"biz-{business.id}-{room_name}"

    token = create_room_token(
        identity=user_id,
        room_name=scoped_room_name,
        metadata=json.dumps(metadata) if metadata else ""
    )

    logger.info(f"Generated LiveKit token for user {user_id}, room {scoped_room_name}")

    return {
        "token": token,
        "room": scoped_room_name,
        "livekit_url": settings.LIVEKIT_URL,
    }