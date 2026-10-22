"""
Contact service.

Business logic layer for Contact management.  All SQL is delegated to
ContactRepository — this file contains zero raw SQLAlchemy imports.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.repositories import ContactRepository
from app.schemas.contact_schema import ContactCreate, ContactUpdate


async def create_contact(
    db: AsyncSession,
    data: ContactCreate,
    auto_commit: bool = True,
) -> Contact:
    """
    Create a new contact.

    `auto_commit=False` is used for bulk CSV imports where the caller
    wants to flush multiple rows and commit once at the end.
    """
    return await ContactRepository(db).create(data, auto_commit=auto_commit)


async def get_contacts_by_business(
    db: AsyncSession,
    business_id: UUID,
    skip: int = 0,
    limit: int = 50,
) -> list[Contact]:
    return await ContactRepository(db).list_by_business(business_id, skip=skip, limit=limit)


async def get_contact(db: AsyncSession, contact_id: UUID) -> Contact | None:
    return await ContactRepository(db).get_by_id(contact_id)


async def update_contact(
    db: AsyncSession, contact_id: UUID, data: ContactUpdate
) -> Contact | None:
    return await ContactRepository(db).update(contact_id, data)


async def delete_contact(db: AsyncSession, contact_id: UUID) -> bool:
    return await ContactRepository(db).delete(contact_id)