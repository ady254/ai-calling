from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.schemas.campaign_schema import CampaignCreate, CampaignUpdate, CampaignOut, CampaignContactAdd
from app.services.campaign_service import (
    create_campaign, get_campaigns_by_business, get_campaign,
    update_campaign, delete_campaign, add_contacts_to_campaign,
    renew_campaign
)
from app.models.business import Business
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business

router = APIRouter()

@router.post("", response_model=CampaignOut)
@router.post("/", response_model=CampaignOut)
async def create_campaign_route(
    data: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    data.business_id = business.id
    return await create_campaign(db, data)

@router.get("", response_model=list[CampaignOut])
@router.get("/", response_model=list[CampaignOut])
async def list_campaigns_route(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    return await get_campaigns_by_business(db, business.id, skip, limit)

@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign_route(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if str(campaign["business_id"]) != str(business.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    return campaign

@router.put("/{campaign_id}", response_model=CampaignOut)
async def update_campaign_route(
    campaign_id: UUID,
    data: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if str(campaign["business_id"]) != str(business.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    
    return await update_campaign(db, campaign_id, data)

@router.delete("/{campaign_id}")
async def delete_campaign_route(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if str(campaign["business_id"]) != str(business.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    
    success = await delete_campaign(db, campaign_id)
    return {"success": success}

@router.post("/{campaign_id}/contacts")
async def add_contacts_route(
    campaign_id: UUID,
    data: CampaignContactAdd,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if str(campaign["business_id"]) != str(business.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    
    await add_contacts_to_campaign(db, campaign_id, data.contact_ids)
    return {"success": True}

@router.post("/{campaign_id}/renew")
async def renew_campaign_route(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if str(campaign["business_id"]) != str(business.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    
    await renew_campaign(db, campaign_id)
    return {"success": True}