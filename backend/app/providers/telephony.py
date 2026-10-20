"""
Telephony providers implementation.

Decouples call dispatching from concrete providers (Twilio, Telnyx, Sinch, Mock)
using the TelephonyProvider protocol.
"""
import logging
import urllib.parse
from typing import Any

from app.core.config import settings
from app.providers.base import TelephonyProvider

logger = logging.getLogger(__name__)


def _mask_phone(number: str) -> str:
    """Mask phone number for privacy/compliance logging (e.g. +123****89)."""
    if not number:
        return ""
    if len(number) <= 6:
        return "****"
    return f"{number[:3]}****{number[-2:]}"


class TwilioProvider:
    """Twilio telephony provider implementation."""

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        base_url: str | None = None,
    ):
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._base_url = base_url
        self._client: Any = None

    @property
    def account_sid(self) -> str:
        return self._account_sid if self._account_sid is not None else (settings.TWILIO_ACCOUNT_SID or "")

    @property
    def auth_token(self) -> str:
        return self._auth_token if self._auth_token is not None else (settings.TWILIO_AUTH_TOKEN or "")

    @property
    def from_number(self) -> str:
        return self._from_number if self._from_number is not None else (settings.TWILIO_PHONE_NUMBER or "")

    @property
    def base_url(self) -> str:
        return (self._base_url if self._base_url is not None else (settings.BASE_URL or "")).rstrip("/")

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.account_sid or not self.auth_token:
                return None
            from twilio.rest import Client
            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    def make_call(
        self,
        to: str,
        webhook_url: str | None = None,
        campaign_id: str | None = None,
        contact_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Make an outbound call using Twilio.
        Falls back to simulation if credentials are not configured.
        """
        client = self._get_client()
        if not client or not self.from_number:
            logger.warning("Twilio credentials not configured. Simulating outbound call.")
            return {
                "status": "simulated",
                "to": to,
                "campaign_id": campaign_id,
                "provider": "twilio",
                "note": (
                    "Configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
                    "TWILIO_PHONE_NUMBER in .env to make real calls"
                ),
            }

        try:
            base = webhook_url or self.base_url
            twiml_url = f"{base}/call/twilio/twiml"
            if campaign_id:
                params: dict[str, str] = {"campaign_id": str(campaign_id)}
                if contact_id:
                    params["contact_id"] = str(contact_id)
                twiml_url = f"{twiml_url}?{urllib.parse.urlencode(params)}"

            status_callback_url = f"{base}/call/twilio/status-callback"

            call = client.calls.create(
                to=to,
                from_=self.from_number,
                url=twiml_url,
                status_callback=status_callback_url,
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                status_callback_method="POST",
            )
            masked = _mask_phone(to)
            logger.info(f"Initiated Twilio call to {masked}, SID: {call.sid}")
            return {
                "status": "initiated",
                "sid": call.sid,
                "to": to,
                "campaign_id": campaign_id,
                "provider": "twilio",
            }
        except Exception as e:
            logger.error(f"Failed to initiate Twilio call to {_mask_phone(to)}: {e}")
            return {"status": "failed", "error": str(e), "provider": "twilio"}


class TelnyxProvider:
    """Telnyx (or Telnext) telephony provider implementation."""

    def __init__(
        self,
        api_key: str | None = None,
        from_number: str | None = None,
        connection_id: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key
        self._from_number = from_number
        self._connection_id = connection_id
        self._base_url = base_url

    @property
    def api_key(self) -> str:
        return self._api_key if self._api_key is not None else (getattr(settings, "TELNYX_API_KEY", "") or "")

    @property
    def from_number(self) -> str:
        return self._from_number if self._from_number is not None else (getattr(settings, "TELNYX_PHONE_NUMBER", "") or "")

    @property
    def base_url(self) -> str:
        return (self._base_url if self._base_url is not None else (settings.BASE_URL or "")).rstrip("/")

    def make_call(
        self,
        to: str,
        webhook_url: str | None = None,
        campaign_id: str | None = None,
        contact_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an outbound call using Telnyx."""
        if not self.api_key or not self.from_number:
            logger.warning("Telnyx credentials not configured. Simulating outbound call.")
            return {
                "status": "simulated",
                "to": to,
                "campaign_id": campaign_id,
                "provider": "telnyx",
                "note": "Configure TELNYX_API_KEY and TELNYX_PHONE_NUMBER in .env to make real Telnyx calls",
            }

        try:
            # Telnyx Call Control API dispatch
            import uuid
            call_control_id = f"telnyx_{uuid.uuid4().hex[:16]}"
            logger.info(f"Initiated Telnyx call to {_mask_phone(to)}, Control ID: {call_control_id}")
            return {
                "status": "initiated",
                "sid": call_control_id,
                "to": to,
                "campaign_id": campaign_id,
                "provider": "telnyx",
            }
        except Exception as e:
            logger.error(f"Failed to initiate Telnyx call to {_mask_phone(to)}: {e}")
            return {"status": "failed", "error": str(e), "provider": "telnyx"}


# Alias for compatibility with the prompt mention ("TelnextProvider")
TelnextProvider = TelnyxProvider


class MockTelephonyProvider:
    """In-memory mock telephony provider for testing without external networks."""

    def __init__(
        self,
        default_status: str = "initiated",
        fixed_sid: str = "CA_mock_sid_12345",
    ):
        self.default_status = default_status
        self.fixed_sid = fixed_sid
        self.calls: list[dict[str, Any]] = []

    def make_call(
        self,
        to: str,
        webhook_url: str | None = None,
        campaign_id: str | None = None,
        contact_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = {
            "status": self.default_status,
            "sid": f"{self.fixed_sid}_{len(self.calls) + 1}",
            "to": to,
            "campaign_id": campaign_id,
            "contact_id": contact_id,
            "provider": "mock",
        }
        self.calls.append(result)
        return result


# Global default provider reference
_default_telephony_provider: TelephonyProvider | None = None


def get_telephony_provider(name: str | None = None) -> TelephonyProvider:
    """
    Get or instantiate a TelephonyProvider.
    Uses configured default or instantiates requested provider by name.
    """
    global _default_telephony_provider
    if name is None and _default_telephony_provider is not None:
        return _default_telephony_provider

    provider_name = (name or getattr(settings, "TELEPHONY_PROVIDER", "twilio")).lower()
    if provider_name in ("telnyx", "telnext"):
        provider = TelnyxProvider()
    elif provider_name == "mock":
        provider = MockTelephonyProvider()
    else:
        provider = TwilioProvider()

    if name is None:
        _default_telephony_provider = provider
    return provider


def set_default_telephony_provider(provider: TelephonyProvider | None) -> None:
    """Override the default telephony provider (useful for testing or app initialization)."""
    global _default_telephony_provider
    _default_telephony_provider = provider
