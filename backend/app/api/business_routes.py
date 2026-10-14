from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.business_schema import BusinessCreate, BusinessUpdate, BusinessOut
from app.services.business_service import create_business, update_business
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business

router = APIRouter()


@router.post("")
@router.post("/")
async def create_business_route(
    data: BusinessCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    return await create_business(db, user_id, data)


@router.get("/me", response_model=BusinessOut)
async def get_my_business_route(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    return await get_user_business(db, user_id)


@router.put("/me", response_model=BusinessOut)
async def update_my_business_route(
    data: BusinessUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    return await update_business(db, business, data)