"""
CallLog repository.

All CallLog-specific SQL lives here — lookup by PK, by call_sid,
by business, and the status/recording update patterns used by
Twilio webhooks.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_log import CallLog
from app.repositories.base import BaseRepository


class CallLogRepository(BaseRepository[CallLog]):
    model = CallLog

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_session(cls, session: AsyncSession) -> "CallLogRepository":
        return cls(session)

    # ── Domain queries ───────────────────────────────────────────────────

    async def get_by_call_sid(self, call_sid: str) -> CallLog | None:
        result = await self._session.execute(
            select(CallLog).where(CallLog.call_sid == call_sid)
        )
        return result.scalar_one_or_none()

    async def get_by_id_and_business(
        self, log_id: UUID, business_id: UUID
    ) -> CallLog | None:
        result = await self._session.execute(
            select(CallLog).where(
                CallLog.id == log_id,
                CallLog.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_business(
        self,
        business_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[CallLog]:
        rows = await self.list_by(
            CallLog.business_id == business_id,
            order_by=CallLog.created_at.desc(),
            offset=skip,
            limit=limit,
        )
        return list(rows)

    async def list_by_campaign(self, campaign_id: UUID) -> list[CallLog]:
        rows = await self.list_by(
            CallLog.campaign_id == campaign_id,
            order_by=CallLog.created_at.desc(),
        )
        return list(rows)

    # ── Write helpers ─────────────────────────────────────────────────────

    async def update_status_by_sid(
        self,
        call_sid: str,
        status: str,
        duration: int,
    ) -> tuple[UUID | None, UUID | None]:
        """
        Update a CallLog's status and duration by Twilio CallSid.

        Returns (contact_id, campaign_id) for the updated row so the
        caller can notify the campaign worker — or (None, None) if not found.
        """
        result = await self._session.execute(
            sa_update(CallLog)
            .where(CallLog.call_sid == call_sid)
            .values(status=status, duration=duration)
            .returning(CallLog.contact_id, CallLog.campaign_id)
        )
        await self._session.commit()
        row = result.first()
        if row:
            return row[0], row[1]
        return None, None

    async def update_recording_by_sid(
        self,
        call_sid: str,
        recording_url: str,
        rec_duration: int,
    ) -> bool:
        """
        Associate a recording URL with the CallLog identified by Twilio CallSid.

        Uses COALESCE so an already-set duration is not overwritten by the
        recording callback's (potentially less accurate) value.
        """
        result = await self._session.execute(
            sa_update(CallLog)
            .where(CallLog.call_sid == call_sid)
            .values(
                recording_url=recording_url,
                duration=func.coalesce(func.nullif(CallLog.duration, 0), rec_duration),
            )
            .returning(CallLog.id)
        )
        await self._session.commit()
        return result.first() is not None

    async def update_outcome(
        self,
        log_id: UUID,
        outcome: str | None,
        summary: str | None,
        follow_up: str | None,
    ) -> None:
        """Store post-call AI-extracted outcome fields."""
        await self._session.execute(
            sa_update(CallLog)
            .where(CallLog.id == log_id)
            .values(outcome=outcome, summary=summary, follow_up=follow_up)
        )
        await self._session.commit()
