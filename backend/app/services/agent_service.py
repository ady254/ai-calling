"""
Agent service.

Business logic layer for Agent management.  All SQL is delegated to
AgentRepository — this file contains zero raw SQLAlchemy imports.
"""
from typing import List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.repositories import AgentRepository
from app.schemas.agent_schema import AgentCreate, AgentUpdate


async def create_agent(db: AsyncSession, data: AgentCreate) -> Agent:
    return await AgentRepository(db).create(data)


async def get_agents_by_business(
    db: AsyncSession, business_id: UUID, skip: int = 0, limit: int = 50
) -> List[Agent]:
    return await AgentRepository(db).list_by_business(business_id, skip=skip, limit=limit)


async def get_agent(db: AsyncSession, agent_id: UUID) -> Agent | None:
    return await AgentRepository(db).get_by_id(agent_id)


async def update_agent(db: AsyncSession, agent_id: UUID, data: AgentUpdate) -> Agent | None:
    return await AgentRepository(db).update(agent_id, data)


async def delete_agent(db: AsyncSession, agent_id: UUID) -> bool:
    return await AgentRepository(db).delete(agent_id)
