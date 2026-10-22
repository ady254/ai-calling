"""
Campaign service.

Business logic layer for Campaign management.  All SQL is delegated to
CampaignRepository — this file contains zero raw SQLAlchemy imports.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.repositories import CampaignRepository
from app.schemas.campaign_schema import CampaignCreate, CampaignOut, CampaignUpdate


async def create_campaign(db: AsyncSession, data: CampaignCreate) -> Campaign:
    return await CampaignRepository(db).create(data)


async def get_campaigns_by_business(
    db: AsyncSession,
    business_id: UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[CampaignOut]:
    return await CampaignRepository(db).list_by_business(business_id, skip=skip, limit=limit)


async def get_campaign(db: AsyncSession, campaign_id: UUID) -> CampaignOut | None:
    return await CampaignRepository(db).get_with_count(campaign_id)


async def update_campaign(
    db: AsyncSession, campaign_id: UUID, data: CampaignUpdate
) -> CampaignOut | None:
    return await CampaignRepository(db).update(campaign_id, data)


async def delete_campaign(db: AsyncSession, campaign_id: UUID) -> bool:
    return await CampaignRepository(db).delete(campaign_id)


async def add_contacts_to_campaign(
    db: AsyncSession, campaign_id: UUID, contact_ids: list[UUID]
) -> bool:
    return await CampaignRepository(db).add_contacts(campaign_id, contact_ids)


async def renew_campaign(db: AsyncSession, campaign_id: UUID) -> bool:
    return await CampaignRepository(db).renew(campaign_id)