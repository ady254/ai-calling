"""
Generic repository base.

Provides a typed, reusable async CRUD foundation that all concrete
repositories inherit.  Every method works against a single SQLAlchemy
model type `T` and an injected `AsyncSession` so:

  1. SQL never leaks into service or route layers.
  2. Adding a new entity type needs only a concrete subclass — zero boilerplate.
  3. The session (and therefore the transaction boundary) stays owned by the
     caller (service or route), not the repository.
"""
from __future__ import annotations

import logging
from typing import Generic, Sequence, Type, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """
    Async CRUD repository for a single SQLAlchemy model.

    Usage::

        class ContactRepository(BaseRepository[Contact]):
            model = Contact

        repo = ContactRepository(db)
        contact = await repo.get_by_id(some_uuid)
    """

    model: Type[T]  # Concrete subclasses must define this class-level attribute.

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Read ─────────────────────────────────────────────────────────────

    async def get_by_id(self, pk: UUID) -> T | None:
        """Return the row with the given primary key, or None."""
        return await self._session.get(self.model, pk)

    async def list_by(
        self,
        *filters,
        order_by=None,
        offset: int = 0,
        limit: int = 50,
    ) -> Sequence[T]:
        """
        Generic filtered list query.

        Accepts arbitrary SQLAlchemy column expressions as `*filters`::

            await repo.list_by(Contact.business_id == biz_id, limit=20)
        """
        stmt = select(self.model)
        if filters:
            stmt = stmt.where(*filters)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def exists(self, *filters) -> bool:
        """Return True if at least one matching row exists."""
        stmt = select(self.model.id).where(*filters).limit(1)  # type: ignore[attr-defined]
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ── Write ────────────────────────────────────────────────────────────

    def add(self, instance: T) -> T:
        """
        Stage an ORM instance for insertion.

        The caller is responsible for calling `await session.commit()` or
        `await session.flush()` to actually persist the row.  This keeps
        transaction boundaries explicit and outside the repository.
        """
        self._session.add(instance)
        return instance

    async def save(self, instance: T) -> T:
        """Persist and refresh a single instance (commit included)."""
        self._session.add(instance)
        await self._session.commit()
        await self._session.refresh(instance)
        return instance

    async def delete_by_id(self, pk: UUID) -> bool:
        """Delete a row by primary key.  Returns True if a row was deleted."""
        instance = await self.get_by_id(pk)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.commit()
        return True
