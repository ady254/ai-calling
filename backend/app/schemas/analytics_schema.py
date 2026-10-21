from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


# ── Shared Sub-models ──────────────────────────────────────────────────

class KPIItem(BaseModel):
    id: str
    title: Optional[str] = None
    label: Optional[str] = None
    value: Any
    subLabel: Optional[str] = None
    changeType: Optional[str] = None
    tone: Optional[str] = None
    icon: Optional[str] = None
    isHero: Optional[bool] = None
    hint: Optional[str] = None


class FunnelStep(BaseModel):
    id: str
    label: str
    value: int


class RecentCallItem(BaseModel):
    id: str
    customer: str
    duration: str
    outcome: str
    leadScore: int = 0
    sentiment: str = "Neutral"
    nextAction: str = "Follow-up"


class TimelineEvent(BaseModel):
    id: str
    type: str
    title: str
    time: str


class CampaignHeaderAnalytics(BaseModel):
    name: str
    status: str
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class CampaignProgressAnalytics(BaseModel):
    completed: int
    total: int
    etaLabel: str
    retryQueue: int
    failedCalls: int
    successRate: int


class CampaignSettingsAnalytics(BaseModel):
    voice: str
    language: str
    retries: str
    knowledgeBase: str = "—"
    promptVersion: str = "v1"
    launchSchedule: str = "Manual"
    timezone: str = "—"


# ── Campaign Details Analytics ─────────────────────────────────────────

class CampaignAnalytics(BaseModel):
    header: CampaignHeaderAnalytics
    kpis: List[KPIItem]
    funnel: List[FunnelStep]
    progress: CampaignProgressAnalytics
    recentCalls: List[RecentCallItem]
    timeline: List[TimelineEvent]
    settings: CampaignSettingsAnalytics


# ── Single Call Detail Analytics ───────────────────────────────────────

class CallHeaderDetail(BaseModel):
    customerName: str
    company: str
    phone: str
    campaign: str
    status: str
    durationLabel: str
    date: Optional[str] = None


class IntelligenceItem(BaseModel):
    id: str
    label: str
    value: str
    tone: Optional[str] = None
    progress: Optional[int] = None


class CallRecordingDetail(BaseModel):
    url: Optional[str] = None
    durationSeconds: int = 0


class TranscriptSegment(BaseModel):
    id: str
    speaker: str
    timestamp: int
    text: str
    tags: List[str] = Field(default_factory=list)


class CallDetailOut(BaseModel):
    header: CallHeaderDetail
    kpis: List[KPIItem]
    intelligence: List[IntelligenceItem]
    recording: Optional[CallRecordingDetail] = None
    summary: Optional[List[str]] = None
    transcript: Optional[List[TranscriptSegment]] = None
