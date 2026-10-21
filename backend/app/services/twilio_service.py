"""
Telephony voice service.

Provides outbound call dispatching backed by injectable TelephonyProvider implementations
(Twilio, Telnyx, Sinch, Mock).
"""
import logging
from typing import Any

from app.providers import TelephonyProvider, get_telephony_provider, TwilioProvider

logger = logging.getLogger(__name__)


def make_outbound_call(
    to_number: str,
    campaign_id: str | None = None,
    contact_id: str | None = None,
    provider: TelephonyProvider | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Make an outbound call using the configured TelephonyProvider (default: Twilio).
    Falls back to simulation if credentials are not configured.

    NOTE: This function is synchronous (some telephony SDKs use `requests` internally).
    Always call it via `asyncio.to_thread(make_outbound_call, ...)` from async code
    to avoid blocking the event loop.
    """
    active_provider = provider or get_telephony_provider()
    return active_provider.make_call(
        to=to_number,
        campaign_id=campaign_id,
        contact_id=contact_id,
        **kwargs,
    )
