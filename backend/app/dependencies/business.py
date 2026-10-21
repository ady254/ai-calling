from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.models.business import Business
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user


async def get_user_business(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> Business:
    """Resolve the current user's business. Shared across all route modules."""
    stmt = select(Business).filter(Business.user_id == UUID(user_id))
    result = await db.execute(stmt)
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found for user")
    return business

