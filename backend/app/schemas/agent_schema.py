from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional
from datetime import datetime


class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: str = Field(..., min_length=1)
    prompt_template: Optional[str] = Field(None, description="Custom outcome extraction prompt template")
    language: str = Field(default="en")
    voice_id: str = Field(default="alloy")
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)


class AgentCreate(AgentBase):
    business_id: Optional[UUID] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    prompt_template: Optional[str] = None
    language: Optional[str] = None
    voice_id: Optional[str] = None
    stability: Optional[float] = Field(None, ge=0, le=1)
    similarity_boost: Optional[float] = Field(None, ge=0, le=1)


class AgentOut(AgentBase):
    id: UUID
    business_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
