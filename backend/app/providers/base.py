"""
Core provider protocols and interfaces.

Defines decoupled abstractions for external I/O services (Telephony, LLM, STT, TTS)
so concrete implementations (Twilio, Telnyx, Gemini, OpenAI, etc.) can be swapped
via configuration or dependency injection without modifying business logic.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TelephonyProvider(Protocol):
    """Interface for outbound telephony providers (Twilio, Telnyx, Sinch, etc.)."""

    def make_call(
        self,
        to: str,
        webhook_url: str | None = None,
        campaign_id: str | None = None,
        contact_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Initiate an outbound phone call.

        Args:
            to: Recipient phone number in E.164 format.
            webhook_url: Base or explicit webhook URL for call routing/TwiML.
            campaign_id: Optional UUID/string of the associated campaign.
            contact_id: Optional UUID/string of the contact being called.
            **kwargs: Provider-specific options.

        Returns:
            Dict containing call metadata (e.g. status, sid, to, campaign_id).
        """
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Interface for LLM completion and structured extraction providers."""

    async def complete(
        self,
        prompt_or_messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        """
        Generate text or JSON completion from a prompt string or message list.

        Args:
            prompt_or_messages: Either a string prompt or list of message dicts.
            **kwargs: Provider-specific generation options (temperature, json mode, etc.).

        Returns:
            The generated response string.
        """
        ...


@runtime_checkable
class STTProvider(Protocol):
    """Interface for Speech-to-Text providers."""

    async def transcribe(self, audio_stream: Any, **kwargs: Any) -> str:
        """Transcribe audio stream or buffer to text."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Interface for Text-to-Speech providers."""

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        **kwargs: Any,
 ) -> bytes:
        """Synthesize text into audio bytes."""
        ...
