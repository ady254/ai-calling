"""
Unit tests for campaign_executor extracted single-responsibility functions (SOLID B1 Problem 2).
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.campaign import CampaignStatus
from app.services.campaign_executor import (
    _check_control_flag,
    _finalize_campaign,
    _mark_calling,
    _process_campaign,
)


@pytest.mark.asyncio
async def test_check_control_flag_when_none_redis():
    campaign_id = uuid.uuid4()
    result = await _check_control_flag(None, campaign_id)
    assert result is None


@pytest.mark.asyncio
async def test_check_control_flag_paused():
    campaign_id = uuid.uuid4()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = b"PAUSED"

    result = await _check_control_flag(mock_redis, campaign_id)
    assert result == "PAUSED"
    mock_redis.get.assert_awaited_once_with(f"campaign:status:{campaign_id}")


@pytest.mark.asyncio
async def test_check_control_flag_cancelled():
    campaign_id = uuid.uuid4()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "CANCELLED"

    result = await _check_control_flag(mock_redis, campaign_id)
    assert result == "CANCELLED"


@pytest.mark.asyncio
async def test_check_control_flag_active_or_none():
    campaign_id = uuid.uuid4()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    result = await _check_control_flag(mock_redis, campaign_id)
    assert result is None


@pytest.mark.asyncio
async def test_process_campaign_orchestration_loop():
    """Verify that _process_campaign calls its single-responsibility helpers in sequence."""
    campaign_id = uuid.uuid4()
    business_id = uuid.uuid4()
    contact_1 = uuid.uuid4()

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Not paused/cancelled

    # Return contact_1 once, then None to stop loop
    pending_sequence = [contact_1, None]

    async def fake_fetch(_camp_id):
        return pending_sequence.pop(0)

    with (
        patch("app.services.campaign_executor._finalize_campaign", new_callable=AsyncMock) as mock_finalize,
        patch("app.services.campaign_executor._check_control_flag", new_callable=AsyncMock) as mock_check,
        patch("app.services.campaign_executor._fetch_next_pending_contact", side_effect=fake_fetch) as mock_fetch,
        patch("app.services.campaign_executor._mark_calling", new_callable=AsyncMock) as mock_mark,
        patch("app.services.campaign_executor._call_single_contact", new_callable=AsyncMock) as mock_call,
        patch("app.services.campaign_executor._wait_for_contact_resolution", new_callable=AsyncMock) as mock_wait,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_check.return_value = None

        await _process_campaign({"redis": mock_redis}, campaign_id, business_id)

        # 1. Started active
        mock_finalize.assert_any_await(campaign_id, CampaignStatus.ACTIVE)

        # 2. Checked control flag
        assert mock_check.await_count >= 1

        # 3. Marked contact_1 calling
        mock_mark.assert_awaited_once_with(campaign_id, contact_1)

        # 4. Placed call
        mock_call.assert_awaited_once_with(
            campaign_id, business_id, contact_1, telephony_provider=None
        )

        # 5. Waited for contact resolution
        mock_wait.assert_awaited_once_with(campaign_id, contact_1, timeout_seconds=300)

        # 6. Finalized completed
        mock_finalize.assert_any_await(campaign_id, CampaignStatus.COMPLETED)
