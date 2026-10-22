"""
Agent repository.

Centralises all Agent-specific SQL.  Services and ownership dependencies
import this; they never write raw `select(Agent)` queries directly.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.repositories.base import BaseRepository
from app.schemas.agent_schema import AgentCreate, AgentUpdate


class AgentRepository(BaseRepository[Agent]):
    model = Agent

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_session(cls, session: AsyncSession) -> "AgentRepository":
        return cls(session)

    # ── Domain queries ───────────────────────────────────────────────────

    async def list_by_business(
        self,
        business_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Agent]:
        rows = await self.list_by(
            Agent.business_id == business_id,
            order_by=Agent.created_at.desc(),
            offset=skip,
            limit=limit,
        )
        return list(rows)

    # ── Write helpers ────────────────────────────────────────────────────

    async def create(self, data: AgentCreate) -> Agent:
        agent = Agent(**data.model_dump())
        return await self.save(agent)

    async def update(self, agent_id: UUID, data: AgentUpdate) -> Agent | None:
        agent = await self.get_by_id(agent_id)
        if not agent:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(agent, key, value)
        await self._session.commit()
        await self._session.refresh(agent)
        return agent

    async def delete(self, agent_id: UUID) -> bool:
        agent = await self.get_by_id(agent_id)
        if not agent:
            return False
        await self._session.delete(agent)
        await self._session.commit()
        return True
