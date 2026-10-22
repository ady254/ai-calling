"""
Contact repository.

All Contact-specific SQL lives here.  Services call these methods; they
never import `select`, `update`, or `delete` directly.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact, ContactStatus
from app.repositories.base import BaseRepository
from app.schemas.contact_schema import ContactCreate, ContactUpdate


class ContactRepository(BaseRepository[Contact]):
    model = Contact

    # ── Factory ─────────────────────────────────────────────────────────

    @classmethod
    def from_session(cls, session: AsyncSession) -> "ContactRepository":
        return cls(session)

    # ── Domain queries ───────────────────────────────────────────────────

    async def list_by_business(
        self,
        business_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Contact]:
        rows = await self.list_by(
            Contact.business_id == business_id,
            order_by=Contact.created_at.desc(),
            offset=skip,
            limit=limit,
        )
        return list(rows)

    async def get_by_id(self, pk: UUID) -> Contact | None:  # type: ignore[override]
        """Session.get uses the identity map — fastest path for PK lookups."""
        return await self._session.get(Contact, pk)

    # ── Write helpers ────────────────────────────────────────────────────

    async def create(
        self,
        data: ContactCreate,
        auto_commit: bool = True,
    ) -> Contact:
        """
        Persist a new Contact.

        `auto_commit=False` lets bulk CSV importers flush many rows then
        commit once, instead of one commit per contact (N-commits fix).
        """
        contact = Contact(
            business_id=data.business_id,
            name=data.name,
            phone_number=data.phone_number,
            email=data.email,
            company=data.company,
            tags=data.tags,
            notes=data.notes,
            industry=getattr(data, "industry", None),
            lead_score=getattr(data, "lead_score", None),
            pipeline_stage=(getattr(data, "pipeline_stage", None) or "new"),
            custom_fields=data.custom_fields or {},
            status=ContactStatus.NEW,
        )
        self._session.add(contact)
        if auto_commit:
            await self._session.commit()
            await self._session.refresh(contact)
        return contact

    async def update(self, contact_id: UUID, data: ContactUpdate) -> Contact | None:
        contact = await self.get_by_id(contact_id)
        if not contact:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(contact, key, value)
        await self._session.commit()
        await self._session.refresh(contact)
        return contact

    async def delete(self, contact_id: UUID) -> bool:
        from sqlalchemy import delete as sa_delete
        stmt = sa_delete(Contact).where(Contact.id == contact_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0
