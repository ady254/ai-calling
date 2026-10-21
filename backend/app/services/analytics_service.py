"""
Analytics service.

Encapsulates all business intelligence calculations, KPI formatting, funnel aggregation,
and call detail structuring, returning typed Pydantic models.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.call_log import CallLog
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact
from app.models.contact import Contact
from app.schemas.analytics_schema import (
    CallDetailOut,
    CallHeaderDetail,
    CallRecordingDetail,
    CampaignAnalytics,
    CampaignHeaderAnalytics,
    CampaignProgressAnalytics,
    CampaignSettingsAnalytics,
    FunnelStep,
    IntelligenceItem,
    KPIItem,
    RecentCallItem,
    TimelineEvent,
    TranscriptSegment,
)
from app.schemas.call_schema import AnalyticsOut
from app.services.campaign_executor import get_campaign_progress

# ── Mapping Tables & Formatting Utilities ──────────────────────────────

_OUTCOME_TO_RECENT = {
    "confirmed": "Appointment",
    "rescheduled": "Callback",
    "callback_requested": "Callback",
    "not_interested": "Not Interested",
    "do_not_call": "Not Interested",
    "wrong_person": "No Answer",
    "incomplete": "Voicemail",
    "other": "Qualified",
}

_CAMPAIGN_STATUS_TO_RUN = {
    "active": "running",
    "paused": "paused",
    "scheduled": "scheduled",
    "completed": "completed",
    "draft": "draft",
    "cancelled": "cancelled",
}

_CALL_STATUS_MAP = {
    "completed": "completed",
    "failed": "failed",
    "started": "in-progress",
}


def _fmt_duration(seconds: Optional[int]) -> str:
    s = int(seconds or 0)
    return f"{s // 60}m {s % 60:02d}s"


def _relative_time(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


def _sentiment_tone(s: Optional[str]) -> str:
    s = (s or "").lower()
    if s == "positive":
        return "positive"
    if s == "negative":
        return "negative"
    return "neutral"


# ── Analytics Service Functions ────────────────────────────────────────

async def get_campaign_analytics(
    db: AsyncSession, campaign_id: UUID, business_id: UUID
) -> CampaignAnalytics:
    """
    Compute full per-campaign analytics: KPIs, funnel steps, progress,
    recent calls, and activity timeline.
    """
    campaign = (
        await db.execute(
            select(Campaign).filter(
                Campaign.id == campaign_id, Campaign.business_id == business_id
            )
        )
    ).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    progress = await get_campaign_progress(db, campaign_id) or {}
    total_contacts = progress.get("total_contacts", 0)
    completed = progress.get("completed", 0)
    failed = progress.get("failed", 0)
    calling = progress.get("calling", 0)
    pending = progress.get("pending", 0)

    logs = (
        await db.execute(
            select(CallLog)
            .where(CallLog.campaign_id == campaign_id)
            .order_by(CallLog.created_at.desc())
        )
    ).scalars().all()

    outcome_counts: Dict[str, int] = {}
    for log in logs:
        if log.outcome:
            outcome_counts[log.outcome] = outcome_counts.get(log.outcome, 0) + 1
    appointments = outcome_counts.get("confirmed", 0)
    interested = (
        appointments
        + outcome_counts.get("rescheduled", 0)
        + outcome_counts.get("callback_requested", 0)
    )

    contacts = (
        await db.execute(
            select(Contact)
            .join(CampaignContact, CampaignContact.contact_id == Contact.id)
            .where(CampaignContact.campaign_id == campaign_id)
        )
    ).scalars().all()
    qualified = sum(1 for c in contacts if (c.lead_score or 0) >= 75)
    converted = sum(1 for c in contacts if c.pipeline_stage == "won")
    contacts_by_id = {str(c.id): c for c in contacts}

    answered = completed
    conversion_rate = round(qualified / answered * 100, 1) if answered > 0 else 0.0
    success_rate = round(completed / (completed + failed) * 100) if (completed + failed) > 0 else 0
    est_pipeline = appointments * 4000

    kpis = [
        KPIItem(id="calls-completed", title="Calls Completed", value=completed, subLabel="Total answered", changeType="positive", icon="calls-completed"),
        KPIItem(id="calls-in-progress", title="Calls In Progress", value=calling, subLabel="Currently active", changeType="neutral", icon="in-progress"),
        KPIItem(id="qualified-leads", title="Qualified Leads", value=qualified, subLabel="Lead Score >75", changeType="neutral", icon="qualified"),
        KPIItem(id="appointments-booked", title="Appointments Booked", value=appointments, subLabel="Confirmed", changeType="neutral", icon="appointments"),
        KPIItem(id="conversion-rate", title="Conversion Rate", value=f"{conversion_rate}%", subLabel="Answered → Qualified", changeType="neutral", icon="conversion"),
        KPIItem(id="estimated-pipeline", title="Estimated Pipeline", value=f"${est_pipeline:,}", subLabel="Generated opportunities", changeType="positive", icon="pipeline", isHero=True),
    ]

    funnel = [
        FunnelStep(id="uploaded", label="Uploaded", value=total_contacts),
        FunnelStep(id="answered", label="Answered", value=answered),
        FunnelStep(id="interested", label="Interested", value=interested),
        FunnelStep(id="qualified", label="Qualified", value=qualified),
        FunnelStep(id="appointments", label="Appointments", value=appointments),
        FunnelStep(id="converted", label="Converted", value=converted),
    ]

    recent_calls: List[RecentCallItem] = []
    for log in logs[:8]:
        c = contacts_by_id.get(str(log.contact_id))
        insights = (c.ai_insights or {}) if c else {}
        recent_calls.append(
            RecentCallItem(
                id=str(log.id),
                customer=c.name if c else "Unknown",
                duration=_fmt_duration(log.duration),
                outcome=_OUTCOME_TO_RECENT.get(log.outcome, "Qualified") if log.outcome else "Voicemail",
                leadScore=(c.lead_score if c else 0) or 0,
                sentiment=insights.get("sentiment", "Neutral"),
                nextAction=log.follow_up or "Follow-up",
            )
        )

    timeline: List[TimelineEvent] = []
    for log in logs[:6]:
        if log.outcome == "confirmed":
            title, ttype = "Appointment booked", "success"
        elif log.outcome in ("callback_requested", "rescheduled"):
            title, ttype = "Customer requested callback", "warning"
        elif log.status == "completed":
            title, ttype = "Call completed", "success"
        elif log.status == "failed":
            title, ttype = "Call failed", "warning"
        else:
            title, ttype = "Call started", "info"
        timeline.append(TimelineEvent(id=str(log.id), type=ttype, title=title, time=_relative_time(log.created_at)))

    status_val = campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status)

    return CampaignAnalytics(
        header=CampaignHeaderAnalytics(
            name=campaign.name,
            status=_CAMPAIGN_STATUS_TO_RUN.get(status_val, "draft"),
            createdAt=campaign.created_at.isoformat() if campaign.created_at else None,
            updatedAt=campaign.updated_at.isoformat() if campaign.updated_at else None,
        ),
        kpis=kpis,
        funnel=funnel,
        progress=CampaignProgressAnalytics(
            completed=completed,
            total=total_contacts,
            etaLabel="In progress" if progress.get("is_running") else "Not running",
            retryQueue=pending,
            failedCalls=failed,
            successRate=success_rate,
        ),
        recentCalls=recent_calls,
        timeline=timeline,
        settings=CampaignSettingsAnalytics(
            voice=campaign.ai_voice or "—",
            language=campaign.language or "en",
            retries=f"{campaign.max_retries} attempts" if campaign.max_retries is not None else "—",
            knowledgeBase="—",
            promptVersion="v1",
            launchSchedule=campaign.scheduled_at.isoformat() if campaign.scheduled_at else "Manual",
            timezone="—",
        ),
    )


async def get_call_detail(
    db: AsyncSession, log_id: UUID, business_id: UUID
) -> CallDetailOut:
    """
    Fetch comprehensive call detail: header, KPIs, conversation intelligence,
    recording status, bulleted summary, and timestamped transcript segments.
    """
    log = (
        await db.execute(
            select(CallLog).filter(CallLog.id == log_id, CallLog.business_id == business_id)
        )
    ).scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Call not found")

    contact = None
    if log.contact_id:
        contact = (
            await db.execute(select(Contact).filter(Contact.id == log.contact_id))
        ).scalar_one_or_none()

    campaign = None
    if log.campaign_id:
        campaign = (
            await db.execute(select(Campaign).filter(Campaign.id == log.campaign_id))
        ).scalar_one_or_none()

    insights = (contact.ai_insights or {}) if contact else {}
    lead_score = contact.lead_score if contact else None
    sentiment = insights.get("sentiment", "Neutral")
    buying_intent = insights.get("buying_intent", "Medium")
    decision_maker = insights.get("decision_maker", "Unknown")
    conv_prob = insights.get("conversion_probability")

    if log.outcome == "confirmed":
        appointment = "Booked"
    elif log.outcome == "rescheduled":
        appointment = "Rescheduled"
    else:
        appointment = "Not booked"
    next_action = log.follow_up or "—"

    status = _CALL_STATUS_MAP.get(log.status, "completed")
    if log.outcome == "wrong_person":
        status = "no-answer"
    elif log.outcome == "incomplete" and log.status != "completed":
        status = "voicemail"

    summary: Optional[List[str]] = None
    if log.summary:
        s = log.summary.strip()
        pts = [seg.strip() for seg in s.split(". ") if seg.strip()]
        summary = [seg if seg.endswith((".", "!", "?")) else seg + "." for seg in pts] or [s]

    transcript_segments: Optional[List[TranscriptSegment]] = None
    if log.transcript:
        lines = [ln.strip() for ln in log.transcript.splitlines() if ln.strip()]
        dur = int(log.duration or 0)
        n = len(lines)
        segs: List[TranscriptSegment] = []
        for i, ln in enumerate(lines):
            low = ln.lower()
            if low.startswith(("agent:", "ai:", "assistant:")):
                speaker, text = "ai", ln.split(":", 1)[1].strip()
            elif low.startswith(("user:", "customer:", "caller:")):
                speaker, text = "customer", ln.split(":", 1)[1].strip()
            else:
                speaker, text = "ai", ln
            ts = round(i * dur / n) if n > 0 else 0
            segs.append(TranscriptSegment(id=f"s{i}", speaker=speaker, timestamp=ts, text=text, tags=[]))
        transcript_segments = segs or None

    recording: Optional[CallRecordingDetail] = None
    if log.recording_url:
        recording = CallRecordingDetail(url=f"/call/logs/{log.id}/recording", durationSeconds=int(log.duration or 0))
    elif (log.duration or 0) > 0:
        recording = CallRecordingDetail(url=None, durationSeconds=int(log.duration or 0))

    kpis = [
        KPIItem(id="lead-score", label="Lead Score", value=f"{lead_score} / 100" if lead_score is not None else "—", hint="Contact score", tone="accent", icon="lead-score"),
        KPIItem(id="buying-intent", label="Buying Intent", value=buying_intent, tone="positive" if buying_intent == "High" else "neutral", icon="buying-intent"),
        KPIItem(id="sentiment", label="Sentiment", value=sentiment, tone=_sentiment_tone(sentiment), icon="sentiment"),
        KPIItem(id="decision-maker", label="Decision Maker", value=decision_maker, tone="neutral", icon="decision-maker"),
        KPIItem(id="appointment", label="Appointment", value=appointment, tone="positive" if appointment == "Booked" else "neutral", icon="appointment"),
        KPIItem(id="next-action", label="Next Action", value=next_action[:40], tone="neutral", icon="next-action"),
    ]

    intelligence = [
        IntelligenceItem(id="i-lead", label="Lead Score", value=str(lead_score) if lead_score is not None else "—", tone="accent", progress=lead_score),
        IntelligenceItem(id="i-intent", label="Buying Intent", value=buying_intent, tone="positive" if buying_intent == "High" else "neutral"),
        IntelligenceItem(id="i-budget", label="Estimated Budget", value=insights.get("estimated_budget", "—"), tone="neutral"),
        IntelligenceItem(id="i-dm", label="Decision Maker", value=decision_maker, tone="neutral"),
        IntelligenceItem(id="i-timeline", label="Expected Closing", value=insights.get("closing_timeline", "—"), tone="neutral"),
        IntelligenceItem(id="i-risk", label="Risk Level", value=insights.get("risk_level", "—"), tone="neutral"),
        IntelligenceItem(id="i-quality", label="Conversation Quality", value=f"{conv_prob}%" if conv_prob is not None else "—", tone="accent", progress=conv_prob),
    ]

    return CallDetailOut(
        header=CallHeaderDetail(
            customerName=contact.name if contact else "Unknown Contact",
            company=(contact.company if contact else "") or "—",
            phone=(contact.phone_number if contact else "") or "—",
            campaign=campaign.name if campaign else "—",
            status=status,
            durationLabel=_fmt_duration(log.duration),
            date=log.created_at.isoformat() if log.created_at else None,
        ),
        kpis=kpis,
        intelligence=intelligence,
        recording=recording,
        summary=summary,
        transcript=transcript_segments,
    )


async def get_business_analytics(db: AsyncSession, business_id: UUID) -> AnalyticsOut:
    """
    Compute business-level high-level analytics KPIs and 7-day call volume trends.
    """
    total_calls = (
        await db.execute(
            select(func.count(CallLog.id)).where(CallLog.business_id == business_id)
        )
    ).scalar() or 0

    completed_calls = (
        await db.execute(
            select(func.count(CallLog.id)).where(
                CallLog.business_id == business_id, CallLog.status == "completed"
            )
        )
    ).scalar() or 0

    failed_calls = (
        await db.execute(
            select(func.count(CallLog.id)).where(
                CallLog.business_id == business_id, CallLog.status == "failed"
            )
        )
    ).scalar() or 0

    avg_duration = (
        await db.execute(
            select(func.avg(CallLog.duration)).where(CallLog.business_id == business_id)
        )
    ).scalar() or 0.0

    active_campaigns = (
        await db.execute(
            select(func.count(Campaign.id)).where(
                Campaign.business_id == business_id,
                Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED]),
            )
        )
    ).scalar() or 0

    total_contacts = (
        await db.execute(
            select(func.count(Contact.id)).where(Contact.business_id == business_id)
        )
    ).scalar() or 0

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=6)
    day_bucket = func.date_trunc("day", CallLog.created_at)

    trend_rows = (
        await db.execute(
            select(
                func.to_char(day_bucket, "Mon DD").label("day"),
                func.count(CallLog.id).label("calls"),
            )
            .where(
                CallLog.business_id == business_id,
                CallLog.created_at >= seven_days_ago,
            )
            .group_by(day_bucket)
            .order_by(day_bucket.asc())
        )
    ).all()

    trends_dict: Dict[str, int] = {}
    for i in range(6, -1, -1):
        day_str = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%b %d")
        trends_dict[day_str] = 0
    for row in trend_rows:
        day_str = row.day.strip()
        if day_str in trends_dict:
            trends_dict[day_str] = row.calls

    return AnalyticsOut(
        total_calls=total_calls,
        completed_calls=completed_calls,
        failed_calls=failed_calls,
        average_duration_seconds=float(avg_duration),
        active_campaigns=active_campaigns,
        total_contacts=total_contacts,
        call_trends=[{"date": k, "calls": v} for k, v in trends_dict.items()],
    )
