"""
Unit tests for analytics_service (SOLID B1 Problem 3 fix).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.campaign import Campaign, CampaignStatus
from app.models.call_log import CallLog
from app.models.contact import Contact
from app.services.analytics_service import (
    get_campaign_analytics,
    get_call_detail,
    get_business_analytics,
)


@pytest.mark.asyncio
async def test_get_campaign_analytics_not_found():
    db = AsyncMock()
    # Mock campaign not found
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    with pytest.raises(HTTPException) as exc_info:
        await get_campaign_analytics(db, uuid.uuid4(), uuid.uuid4())
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_campaign_analytics_success():
    db = AsyncMock()
    campaign_id = uuid.uuid4()
    business_id = uuid.uuid4()

    mock_campaign = Campaign(
        id=campaign_id,
        business_id=business_id,
        name="Q3 Outreach",
        status=CampaignStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        ai_voice="Bella",
        language="en",
        max_retries=2,
    )

    # 1st execute: campaign query
    # 2nd execute: call logs query
    # 3rd execute: contacts query
    res_campaign = MagicMock()
    res_campaign.scalar_one_or_none.return_value = mock_campaign

    contact_id = uuid.uuid4()
    mock_log = CallLog(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        business_id=business_id,
        contact_id=contact_id,
        outcome="confirmed",
        status="completed",
        duration=125,
        created_at=datetime.now(timezone.utc),
        follow_up="Send catalog",
    )
    res_logs = MagicMock()
    res_logs.scalars.return_value.all.return_value = [mock_log]

    mock_contact = Contact(
        id=contact_id,
        business_id=business_id,
        name="Alice Doe",
        lead_score=85,
        pipeline_stage="won",
        ai_insights={"sentiment": "Positive"},
    )
    res_contacts = MagicMock()
    res_contacts.scalars.return_value.all.return_value = [mock_contact]

    db.execute.side_effect = [res_campaign, res_logs, res_contacts]

    with patch("app.services.analytics_service.get_campaign_progress", new_callable=AsyncMock) as mock_prog:
        mock_prog.return_value = {
            "total_contacts": 1,
            "completed": 1,
            "failed": 0,
            "calling": 0,
            "pending": 0,
            "is_running": True,
        }

        analytics = await get_campaign_analytics(db, campaign_id, business_id)

    assert analytics.header.name == "Q3 Outreach"
    assert analytics.header.status == "running"
    assert analytics.progress.completed == 1
    assert analytics.progress.total == 1
    assert len(analytics.kpis) == 6
    assert len(analytics.funnel) == 6
    assert len(analytics.recentCalls) == 1
    assert analytics.recentCalls[0].customer == "Alice Doe"
    assert analytics.recentCalls[0].outcome == "Appointment"
    assert analytics.recentCalls[0].sentiment == "Positive"


@pytest.mark.asyncio
async def test_get_call_detail_success():
    db = AsyncMock()
    log_id = uuid.uuid4()
    business_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    campaign_id = uuid.uuid4()

    mock_log = CallLog(
        id=log_id,
        business_id=business_id,
        contact_id=contact_id,
        campaign_id=campaign_id,
        status="completed",
        outcome="confirmed",
        duration=90,
        summary="User agreed to buy. Follow up on Monday.",
        transcript="Agent: Hello!\nUser: Yes I agree.",
        recording_url="http://twilio.com/rec123",
        created_at=datetime.now(timezone.utc),
    )
    mock_contact = Contact(
        id=contact_id,
        business_id=business_id,
        name="Bob Smith",
        company="Acme Corp",
        phone_number="+15551234567",
        lead_score=90,
        ai_insights={"sentiment": "Positive", "buying_intent": "High"},
    )
    mock_campaign = Campaign(
        id=campaign_id,
        business_id=business_id,
        name="Enterprise Sales",
    )

    r_log = MagicMock()
    r_log.scalar_one_or_none.return_value = mock_log
    r_contact = MagicMock()
    r_contact.scalar_one_or_none.return_value = mock_contact
    r_campaign = MagicMock()
    r_campaign.scalar_one_or_none.return_value = mock_campaign

    db.execute.side_effect = [r_log, r_contact, r_campaign]

    detail = await get_call_detail(db, log_id, business_id)

    assert detail.header.customerName == "Bob Smith"
    assert detail.header.company == "Acme Corp"
    assert detail.header.status == "completed"
    assert detail.recording is not None
    assert detail.recording.url == f"/call/logs/{log_id}/recording"
    assert detail.summary == ["User agreed to buy.", "Follow up on Monday."]
    assert detail.transcript is not None
    assert len(detail.transcript) == 2
    assert detail.transcript[0].speaker == "ai"
    assert detail.transcript[1].speaker == "customer"


@pytest.mark.asyncio
async def test_get_business_analytics_success():
    db = AsyncMock()
    business_id = uuid.uuid4()

    r1 = MagicMock()
    r1.scalar.return_value = 10  # total_calls
    r2 = MagicMock()
    r2.scalar.return_value = 8   # completed_calls
    r3 = MagicMock()
    r3.scalar.return_value = 2   # failed_calls
    r4 = MagicMock()
    r4.scalar.return_value = 45.5 # avg_duration
    r5 = MagicMock()
    r5.scalar.return_value = 3   # active_campaigns
    r6 = MagicMock()
    r6.scalar.return_value = 100 # total_contacts
    r7 = MagicMock()
    r7.all.return_value = []     # trend_rows

    db.execute.side_effect = [r1, r2, r3, r4, r5, r6, r7]

    res = await get_business_analytics(db, business_id)
    assert res.total_calls == 10
    assert res.completed_calls == 8
    assert res.failed_calls == 2
    assert res.average_duration_seconds == 45.5
    assert res.active_campaigns == 3
    assert res.total_contacts == 100
    assert len(res.call_trends) == 7
