"""
Repository layer — public API.

Import from here throughout the codebase so import paths never change
if an individual repository file is moved or renamed::

    from app.repositories import ContactRepository, CampaignRepository
"""
from app.repositories.agent_repository import AgentRepository
from app.repositories.call_log_repository import CallLogRepository
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.contact_repository import ContactRepository

__all__ = [
    "AgentRepository",
    "CallLogRepository",
    "CampaignRepository",
    "ContactRepository",
]
