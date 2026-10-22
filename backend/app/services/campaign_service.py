from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import selectinload
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact
from app.schemas.campaign_schema import CampaignCreate, CampaignUpdate, CampaignOut
from uuid import UUID

async def create_campaign(db: AsyncSession, data: CampaignCreate) -> Campaign:
    campaign = Campaign(
        business_id=data.business_id,
        name=data.name,
        description=data.description,
        objective=data.objective,
        language=data.language,
        ai_prompt=data.ai_prompt,
        ai_voice=data.ai_voice,
        max_retries=data.max_retries,
        scheduled_at=data.scheduled_at,
        status=CampaignStatus.DRAFT
    )
    db.add(campaign)
    await db.flush()

    if data.contact_ids:
        for contact_id in data.contact_ids:
            cc = CampaignContact(campaign_id=campaign.id, contact_id=contact_id)
            db.add(cc)

    await db.commit()
    await db.refresh(campaign)
    return campaign

async def get_campaigns_by_business(
    db: AsyncSession,
    business_id: UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[CampaignOut]:
    # Get campaigns with contact count
    stmt = select(
        Campaign,
        func.count(CampaignContact.id).label('contact_count')
    ).outerjoin(
        CampaignContact, Campaign.id == CampaignContact.campaign_id
    ).filter(
        Campaign.business_id == business_id
    ).group_by(Campaign.id).order_by(Campaign.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    campaigns: list[CampaignOut] = []
    for campaign, contact_count in rows:
        c_out = CampaignOut.model_validate(campaign)
        c_out.contact_count = contact_count
        campaigns.append(c_out)

    return campaigns

async def get_campaign(db: AsyncSession, campaign_id: UUID) -> CampaignOut | None:
    stmt = select(
        Campaign,
        func.count(CampaignContact.id).label('contact_count')
    ).outerjoin(
        CampaignContact, Campaign.id == CampaignContact.campaign_id
    ).filter(
        Campaign.id == campaign_id
    ).group_by(Campaign.id)

    result = await db.execute(stmt)
    row = result.first()
    if row:
        campaign, contact_count = row
        c_out = CampaignOut.model_validate(campaign)
        c_out.contact_count = contact_count
        return c_out
    return None

async def update_campaign(db: AsyncSession, campaign_id: UUID, data: CampaignUpdate) -> CampaignOut | None:
    stmt = select(Campaign).filter(Campaign.id == campaign_id)
    result = await db.execute(stmt)
    campaign = result.scalar_one_or_none()

    if not campaign:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(campaign, key, value)

    await db.commit()
    await db.refresh(campaign)
    return await get_campaign(db, campaign_id)

async def delete_campaign(db: AsyncSession, campaign_id: UUID):
    stmt = delete(Campaign).where(Campaign.id == campaign_id)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount > 0

async def add_contacts_to_campaign(db: AsyncSession, campaign_id: UUID, contact_ids: list[UUID]) -> bool:
    """
    Add contacts to a campaign.

    Fix #20: The old implementation ran one SELECT per contact to check for
    duplicates (N+1 queries). This version uses a single PostgreSQL
    INSERT … ON CONFLICT DO NOTHING, which is both correct and O(1) queries
    regardless of how many contacts are being added.
    """
    if not contact_ids:
        return True

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    rows = [
        {"campaign_id": campaign_id, "contact_id": cid}
        for cid in contact_ids
    ]
    stmt = pg_insert(CampaignContact).values(rows).on_conflict_do_nothing(
        index_elements=["campaign_id", "contact_id"]
    )
    await db.execute(stmt)
    await db.commit()
    return True

async def renew_campaign(db: AsyncSession, campaign_id: UUID) -> bool:
    """
    Renews a campaign by resetting its status to DRAFT and all its contacts' call_status to PENDING.
    """
    # 1. Update Campaign status
    stmt = update(Campaign).where(Campaign.id == campaign_id).values(
        status=CampaignStatus.DRAFT
    )
    await db.execute(stmt)

    # 2. Update CampaignContact status
    stmt2 = update(CampaignContact).where(CampaignContact.campaign_id == campaign_id).values(
        call_status="PENDING",
        called_at=None,
        twilio_call_sid=None
    )
    await db.execute(stmt2)

    await db.commit()
    return True