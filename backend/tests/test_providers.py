"""
Unit tests for external provider interfaces and dependency injection (SOLID B1 fix).

Tests verify that:
1. External service SDKs (Twilio, Gemini, etc.) are NOT instantiated at import time.
2. Concrete providers implement runtime-checkable protocols (TelephonyProvider, LLMProvider).
3. Providers can be injected via parameters or default setters without monkey-patching.
4. Fallbacks, simulations, and multi-provider swapping (Twilio vs Telnyx/Telnext vs Mock) work seamlessly.
"""
import pytest
from app.providers.base import TelephonyProvider, LLMProvider, STTProvider, TTSProvider
from app.providers.telephony import (
    TwilioProvider,
    TelnyxProvider,
    TelnextProvider,
    MockTelephonyProvider,
    get_telephony_provider,
    set_default_telephony_provider,
)
from app.providers.llm import (
    GeminiLLMProvider,
    OpenAILLMProvider,
    MockLLMProvider,
    get_llm_provider,
    set_default_llm_provider,
)
from app.services.ai_service import extract_call_outcome
from app.services.twilio_service import make_outbound_call


# ── Protocol Compliance Tests ──────────────────────────────────────────

def test_telephony_providers_satisfy_protocol():
    assert isinstance(TwilioProvider(), TelephonyProvider)
    assert isinstance(TelnyxProvider(), TelephonyProvider)
    assert isinstance(TelnextProvider(), TelephonyProvider)
    assert isinstance(MockTelephonyProvider(), TelephonyProvider)


def test_llm_providers_satisfy_protocol():
    assert isinstance(GeminiLLMProvider(), LLMProvider)
    assert isinstance(OpenAILLMProvider(), LLMProvider)
    assert isinstance(MockLLMProvider(), LLMProvider)


# ── Telephony Provider Dependency Injection ────────────────────────────

def test_telephony_provider_simulation_without_credentials():
    provider = TwilioProvider(account_sid="", auth_token="", from_number="")
    result = provider.make_call(to="+15551234567", campaign_id="camp_123")
    assert result["status"] == "simulated"
    assert result["to"] == "+15551234567"
    assert result["campaign_id"] == "camp_123"
    assert "Configure TWILIO_ACCOUNT_SID" in result["note"]


def test_telnyx_provider_simulation():
    provider = TelnyxProvider(api_key="", from_number="")
    result = provider.make_call(to="+15551234567", campaign_id="camp_456")
    assert result["status"] == "simulated"
    assert result["provider"] == "telnyx"


def test_mock_telephony_provider_tracks_calls():
    mock_provider = MockTelephonyProvider(default_status="initiated", fixed_sid="CA_test")
    res1 = mock_provider.make_call(to="+15550000001", campaign_id="c1", contact_id="cnt1")
    res2 = mock_provider.make_call(to="+15550000002", campaign_id="c1", contact_id="cnt2")

    assert res1["status"] == "initiated"
    assert res1["sid"] == "CA_test_1"
    assert res2["sid"] == "CA_test_2"
    assert len(mock_provider.calls) == 2


def test_make_outbound_call_accepts_injected_provider():
    mock_provider = MockTelephonyProvider(fixed_sid="CA_injected")
    result = make_outbound_call(
        to_number="+15559998888",
        campaign_id="camp_test",
        contact_id="cnt_test",
        provider=mock_provider,
    )
    assert result["status"] == "initiated"
    assert result["sid"] == "CA_injected_1"
    assert len(mock_provider.calls) == 1
    assert mock_provider.calls[0]["to"] == "+15559998888"


def test_set_default_telephony_provider():
    mock = MockTelephonyProvider(fixed_sid="CA_global_default")
    original = get_telephony_provider()
    try:
        set_default_telephony_provider(mock)
        assert get_telephony_provider() is mock
        res = make_outbound_call("+15551112222")
        assert res["sid"] == "CA_global_default_1"
    finally:
        set_default_telephony_provider(original)


# ── LLM Provider Dependency Injection (No Monkeypatching) ──────────────

@pytest.mark.asyncio
async def test_extract_call_outcome_with_injected_llm_provider():
    transcript = (
        "Agent: Hi, calling to confirm your appointment for Friday.\n"
        "User: Yes, I will be there at 3pm."
    )
    mock_llm = MockLLMProvider(
        response='{"outcome": "confirmed", "summary": "User confirmed Friday 3pm.", "follow_up": null}'
    )

    result = await extract_call_outcome(transcript, llm_provider=mock_llm)

    assert result == {
        "outcome": "confirmed",
        "summary": "User confirmed Friday 3pm.",
        "follow_up": None,
    }
    assert len(mock_llm.calls) == 1
    assert "Agent: Hi, calling to confirm" in mock_llm.calls[0]


@pytest.mark.asyncio
async def test_extract_call_outcome_with_global_llm_provider_override():
    transcript = "Agent: Would you like to reschedule?\nUser: No, do not call me again."
    mock_llm = MockLLMProvider(
        response='{"outcome": "do_not_call", "summary": "Requested DND.", "follow_up": "Remove from list"}'
    )

    original_provider = get_llm_provider()
    try:
        set_default_llm_provider(mock_llm)
        # Call without passing llm_provider — should use the default injected mock
        result = await extract_call_outcome(transcript)
        assert result["outcome"] == "do_not_call"
        assert result["follow_up"] == "Remove from list"
        assert len(mock_llm.calls) == 1
    finally:
        set_default_llm_provider(original_provider)


def test_gemini_client_is_not_initialized_at_import():
    # Instantiating GeminiLLMProvider should NOT create the internal SDK client immediately
    provider = GeminiLLMProvider(api_key="test_dummy_key")
    assert provider._client is None
