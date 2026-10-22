"""
Campaign repository.

All Campaign and CampaignContact SQL lives here — including the
aggregated contact-count query that previously appeared in two separate
service functions and had to be kept in sync manually.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact, CampaignContactStatus
from app.repositories.base import BaseRepository
from app.schemas.campaign_schema import CampaignCreate, CampaignOut, CampaignUpdate


class CampaignRepository(BaseRepository[Campaign]):
    model = Campaign

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_session(cls, session: AsyncSession) -> "CampaignRepository":
        return cls(session)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _with_contact_count(self):
        """Return a base SELECT that outer-joins CampaignContact and GROUP BYs id."""
        return (
            select(Campaign, func.count(CampaignContact.id).label("contact_count"))
            .outerjoin(CampaignContact, Campaign.id == CampaignContact.campaign_id)
            .group_by(Campaign.id)
        )

    def _row_to_out(self, campaign: Campaign, contact_count: int) -> CampaignOut:
        out = CampaignOut.model_validate(campaign)
        out.contact_count = contact_count
        return out

    # ── Domain queries ───────────────────────────────────────────────────

    async def get_with_count(self, campaign_id: UUID) -> CampaignOut | None:
        """Single campaign lookup with contact_count in one query."""
        stmt = self._with_contact_count().where(Campaign.id == campaign_id)
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        campaign, count = row
        return self._row_to_out(campaign, count)

    async def list_by_business(
        self,
        business_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[CampaignOut]:
        stmt = (
            self._with_contact_count()
            .where(Campaign.business_id == business_id)
            .order_by(Campaign.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._row_to_out(c, cnt) for c, cnt in result.all()]

    # ── Write helpers ────────────────────────────────────────────────────

    async def create(self, data: CampaignCreate) -> Campaign:
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
            status=CampaignStatus.DRAFT,
        )
        self._session.add(campaign)
        await self._session.flush()  # obtain PK before inserting contacts

        if data.contact_ids:
            self._session.add_all(
                CampaignContact(campaign_id=campaign.id, contact_id=cid)
                for cid in data.contact_ids
            )

        await self._session.commit()
        await self._session.refresh(campaign)
        return campaign

    async def update(self, campaign_id: UUID, data: CampaignUpdate) -> CampaignOut | None:
        campaign = await self.get_by_id(campaign_id)
        if not campaign:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(campaign, key, value)
        await self._session.commit()
        # Re-fetch with count so the caller always gets a CampaignOut.
        return await self.get_with_count(campaign_id)

    async def delete(self, campaign_id: UUID) -> bool:
        stmt = sa_delete(Campaign).where(Campaign.id == campaign_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0

    async def set_status(self, campaign_id: UUID, status: CampaignStatus) -> None:
        """Update campaign status without loading the full ORM object."""
        await self._session.execute(
            sa_update(Campaign).where(Campaign.id == campaign_id).values(status=status)
        )
        await self._session.commit()

    async def renew(self, campaign_id: UUID) -> bool:
        """Reset campaign to DRAFT and all its contacts back to PENDING."""
        await self._session.execute(
            sa_update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.DRAFT)
        )
        await self._session.execute(
            sa_update(CampaignContact)
            .where(CampaignContact.campaign_id == campaign_id)
            .values(call_status=CampaignContactStatus.PENDING, called_at=None, twilio_call_sid=None)
        )
        await self._session.commit()
        return True

    # ── CampaignContact sub-queries ──────────────────────────────────────

    async def add_contacts(self, campaign_id: UUID, contact_ids: list[UUID]) -> bool:
        """
        Upsert contacts into a campaign.

        Uses a single PostgreSQL INSERT … ON CONFLICT DO NOTHING so duplicate
        contact additions are silently skipped in one round-trip (N+1 fix).
        """
        if not contact_ids:
            return True
        rows = [{"campaign_id": campaign_id, "contact_id": cid} for cid in contact_ids]
        stmt = pg_insert(CampaignContact).values(rows).on_conflict_do_nothing(
            index_elements=["campaign_id", "contact_id"]
        )
        await self._session.execute(stmt)
        await self._session.commit()
        return True

    async def get_contact_status_counts(self, campaign_id: UUID) -> dict[str, int]:
        """Single GROUP BY query replacing N per-status queries."""
        rows = (
            await self._session.execute(
                select(CampaignContact.call_status, func.count(CampaignContact.id))
                .where(CampaignContact.campaign_id == campaign_id)
                .group_by(CampaignContact.call_status)
            )
        ).all()
        return {str(row[0].value): row[1] for row in rows}

    async def list_pending_contact_ids(self, campaign_id: UUID) -> list[UUID]:
        """FIFO list of all pending contact IDs for a campaign."""
        result = await self._session.execute(
            select(CampaignContact.contact_id)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.call_status == CampaignContactStatus.PENDING,
            )
            .order_by(CampaignContact.created_at.asc())
        )
        return list(result.scalars().all())

    async def set_contact_status(
        self,
        campaign_id: UUID,
        contact_id: UUID,
        status: CampaignContactStatus,
    ) -> None:
        """Update a single CampaignContact's call_status."""
        await self._session.execute(
            sa_update(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
            .values(call_status=status)
        )
        await self._session.commit()

    async def get_contact_status(
        self, campaign_id: UUID, contact_id: UUID
    ) -> CampaignContactStatus | None:
        result = await self._session.execute(
            select(CampaignContact.call_status).where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
        )
        return result.scalar_one_or_none()
