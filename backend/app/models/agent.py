from sqlalchemy import Column, String, ForeignKey, DateTime, Text, Float, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime, timezone

from app.db.base import Base


class Agent(Base):

    __tablename__ = "agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=False)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # AI configuration
    system_prompt = Column(Text, nullable=False)
    prompt_template = Column(Text, nullable=True)
    prompt_template_version = Column(Integer, default=1, nullable=False)
    outcome_schema = Column(Text, nullable=True)
    language = Column(String(50), default="en")
    voice_id = Column(String(100), default="alloy")
    
    # ElevenLabs style settings (optional but good for "enhanced experience")
    stability = Column(Float, default=0.5)
    similarity_boost = Column(Float, default=0.75)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    business = relationship("Business", backref="agents")
    campaigns = relationship("Campaign", back_populates="agent")
