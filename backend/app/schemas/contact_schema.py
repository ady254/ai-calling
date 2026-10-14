from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional
from datetime import datetime

from app.models.contact import ContactStatus


class ContactCreate(BaseModel):
    business_id: Optional[UUID] = None
    name: str = Field(..., min_length=1, max_length=255)
    phone_number: str = Field(..., min_length=1, max_length=50)
    email: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    # CRM fields (optional — default handled server-side)
    industry: Optional[str] = Field(None, max_length=120)
    lead_score: Optional[int] = Field(None, ge=0, le=100)
    pipeline_stage: Optional[str] = Field(None, max_length=20)
    # Free-form per-contact variables (e.g. doctor_name, appointment_date,
    # department, preferred_language) used to personalize campaign scripts.
    custom_fields: Optional[dict] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone_number: Optional[str] = Field(None, min_length=1, max_length=50)
    email: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    # Typed against the enum so an invalid value is rejected here with a
    # clean 422 instead of reaching the DB and failing with an unhandled
    # asyncpg.InvalidTextRepresentationError -> 500.
    status: Optional[ContactStatus] = None
    industry: Optional[str] = Field(None, max_length=120)
    lead_score: Optional[int] = Field(None, ge=0, le=100)
    pipeline_stage: Optional[str] = Field(None, max_length=20)
    custom_fields: Optional[dict] = None


class ContactOut(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    phone_number: str
    email: Optional[str] = None
    company: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    status: str
    industry: Optional[str] = None
    lead_score: Optional[int] = None
    pipeline_stage: Optional[str] = None
    ai_insights: Optional[dict] = None
    custom_fields: Optional[dict] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True