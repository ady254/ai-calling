"""
Shared entity ownership dependencies.
Enforces multi-tenant ownership checks (Contact, Agent, Campaign) at the route layer,
eliminating repetitive 404/403 boilerplate across route handlers.
"""
from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.database import get_db
from app.dependencies.business import get_user_business
from app.models.business import Business
from app.models.contact import Contact
from app.models.agent import Agent
from app.services.contact_service import get_contact
from app.services.agent_service import get_agent
from app.services.campaign_service import get_campaign


async def get_owned_contact(
    contact_id: UUID,
    business: Business = Depends(get_user_business),
    db: AsyncSession = Depends(get_db),
) -> Contact:
    """Validate contact existence and ensure it belongs to the authenticated user's business."""
    contact = await get_contact(db, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if contact.business_id != business.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this contact")
    return contact


async def get_owned_agent(
    agent_id: UUID,
    business: Business = Depends(get_user_business),
    db: AsyncSession = Depends(get_db),
) -> Agent:
    """Validate agent existence and ensure it belongs to the authenticated user's business."""
    agent = await get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.business_id != business.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this agent")
    return agent


async def get_owned_campaign(
    campaign_id: UUID,
    business: Business = Depends(get_user_business),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Validate campaign existence and ensure it belongs to the authenticated user's business."""
    campaign = await get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["business_id"] != business.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this campaign")
    return campaign
