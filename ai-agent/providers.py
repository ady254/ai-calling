"""
AI Agent Provider Interfaces & Implementations.

Decouples speech recognition (STT), language reasoning (LLM), and speech synthesis (TTS)
from inline concrete initializations in the agent entrypoint.
Allows plugging in alternate providers (Deepgram, OpenAI, Google, ElevenLabs, Cartesia, Mock)
without editing or forking the main conversational loop.
"""
import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("voice-agent.providers")


# ── Protocols / Interfaces ─────────────────────────────────────────────

@runtime_checkable
class STTProvider(Protocol):
    """Protocol for Speech-to-Text providers."""

    async def transcribe(self, audio_stream: Any, **kwargs: Any) -> str:
        """Transcribe audio to text."""
        ...

    def build_stt(self, language: str = "en", **kwargs: Any) -> Any:
        """Build LiveKit STT plugin instance."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for Large Language Model providers."""

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        """Generate a response text for conversation messages."""
        ...

    def build_llm(self, **kwargs: Any) -> Any:
        """Build LiveKit LLM plugin instance."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Protocol for Text-to-Speech providers."""

    async def synthesize(
        self, text: str, voice_id: str | None = None, **kwargs: Any
    ) -> bytes:
        """Synthesize text to audio bytes."""
        ...

    def build_tts(
        self,
        voice_id: str,
        language: str = "en",
        **kwargs: Any,
    ) -> Any:
        """Build LiveKit TTS plugin instance."""
        ...


# ── STT Provider Implementations ──────────────────────────────────────

class DeepgramSTTProvider:
    """Deepgram Speech-to-Text provider."""

    def __init__(self, default_model: str = "nova-3"):
        self.default_model = default_model

    def build_stt(self, language: str = "en", **kwargs: Any) -> Any:
        from livekit.plugins import deepgram

        model = kwargs.get("model", self.default_model)
        logger.info(f"Initializing Deepgram STT (model={model}, language={language})")
        return deepgram.STT(model=model, language=language)

    async def transcribe(self, audio_stream: Any, **kwargs: Any) -> str:
        # Direct transcription interface if needed outside streaming
        return ""


class OpenAISTTProvider:
    """OpenAI Whisper Speech-to-Text provider."""

    def __init__(self, default_model: str = "whisper-1"):
        self.default_model = default_model

    def build_stt(self, language: str = "en", **kwargs: Any) -> Any:
        try:
            from livekit.plugins import openai
            model = kwargs.get("model", self.default_model)
            logger.info(f"Initializing OpenAI STT (model={model}, language={language})")
            return openai.STT(model=model, language=language)
        except (ImportError, AttributeError):
            logger.warning("OpenAI LiveKit plugin not available, falling back to Deepgram")
            return DeepgramSTTProvider().build_stt(language=language, **kwargs)

    async def transcribe(self, audio_stream: Any, **kwargs: Any) -> str:
        return ""


class MockSTTProvider:
    """Mock STT provider for unit testing without live audio."""

    def __init__(self, mock_transcript: str = "Hello, I am testing."):
        self.mock_transcript = mock_transcript

    def build_stt(self, language: str = "en", **kwargs: Any) -> Any:
        return self

    async def transcribe(self, audio_stream: Any, **kwargs: Any) -> str:
        return self.mock_transcript


# ── LLM Provider Implementations ──────────────────────────────────────

class GoogleLLMProvider:
    """Google Gemini LLM provider for LiveKit agents."""

    def __init__(
        self,
        default_model: str | None = None,
        api_key: str | None = None,
    ):
        self.default_model = default_model
        self.api_key = api_key

    def build_llm(self, **kwargs: Any) -> Any:
        from livekit.plugins import google

        model = (
            kwargs.get("model")
            or self.default_model
            or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        )
        api_key = (
            kwargs.get("api_key")
            or self.api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        logger.info(f"Initializing Google Gemini LLM (model={model})")
        return google.LLM(model=model, api_key=api_key)

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        return ""


class OpenAILLMProvider:
    """OpenAI LLM provider for LiveKit agents."""

    def __init__(
        self,
        default_model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ):
        self.default_model = default_model
        self.api_key = api_key

    def build_llm(self, **kwargs: Any) -> Any:
        try:
            from livekit.plugins import openai

            model = kwargs.get("model") or self.default_model
            api_key = kwargs.get("api_key") or self.api_key or os.getenv("OPENAI_API_KEY")
            logger.info(f"Initializing OpenAI LLM (model={model})")
            return openai.LLM(model=model, api_key=api_key)
        except (ImportError, AttributeError):
            logger.warning("OpenAI LiveKit plugin not available, falling back to Google LLM")
            return GoogleLLMProvider().build_llm(**kwargs)

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        return ""


class MockLLMProvider:
    """Mock LLM provider for unit testing."""

    def __init__(self, response: str = "Sure, I can help with that."):
        self.response = response

    def build_llm(self, **kwargs: Any) -> Any:
        return self

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        return self.response


# ── TTS Provider Implementations ──────────────────────────────────────

class ElevenLabsTTSProvider:
    """ElevenLabs Text-to-Speech provider for LiveKit agents."""

    def __init__(
        self,
        default_model: str | None = None,
        api_key: str | None = None,
    ):
        self.default_model = default_model
        self.api_key = api_key

    def build_tts(
        self,
        voice_id: str,
        language: str = "en",
        **kwargs: Any,
    ) -> Any:
        from livekit.plugins import elevenlabs
        from livekit.plugins.elevenlabs.tts import VoiceSettings

        model = (
            kwargs.get("model")
            or self.default_model
            or os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
        )
        api_key = kwargs.get("api_key") or self.api_key or os.getenv("ELEVEN_API_KEY")

        stability = float(kwargs.get("stability", os.getenv("ELEVENLABS_STABILITY", "0.60")))
        similarity_boost = float(kwargs.get("similarity_boost", os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.82")))
        style = float(kwargs.get("style", os.getenv("ELEVENLABS_STYLE", "0.0")))
        speed = float(kwargs.get("speed", os.getenv("ELEVENLABS_SPEED", "1.0")))
        speaker_boost = kwargs.get("use_speaker_boost")
        if speaker_boost is None:
            speaker_boost = os.getenv("ELEVENLABS_SPEAKER_BOOST", "true").lower() == "true"

        logger.info(f"Initializing ElevenLabs TTS (model={model}, voice_id={voice_id})")
        return elevenlabs.TTS(
            model=model,
            voice_id=voice_id,
            api_key=api_key,
            encoding="pcm_16000",
            language=language,
            auto_mode=True,
            voice_settings=VoiceSettings(
                stability=stability,
                similarity_boost=similarity_boost,
                style=style,
                speed=speed,
                use_speaker_boost=speaker_boost,
            ),
        )

    async def synthesize(
        self, text: str, voice_id: str | None = None, **kwargs: Any
    ) -> bytes:
        return b""


class CartesiaTTSProvider:
    """Cartesia Text-to-Speech provider for ultra-low latency calls."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def build_tts(
        self,
        voice_id: str,
        language: str = "en",
        **kwargs: Any,
    ) -> Any:
        try:
            from livekit.plugins import cartesia
            api_key = kwargs.get("api_key") or self.api_key or os.getenv("CARTESIA_API_KEY")
            logger.info(f"Initializing Cartesia TTS (voice_id={voice_id})")
            return cartesia.TTS(voice=voice_id, api_key=api_key)
        except (ImportError, AttributeError):
            logger.warning("Cartesia plugin not available, falling back to ElevenLabs")
            return ElevenLabsTTSProvider().build_tts(voice_id=voice_id, language=language, **kwargs)

    async def synthesize(
        self, text: str, voice_id: str | None = None, **kwargs: Any
    ) -> bytes:
        return b""


class MockTTSProvider:
    """Mock TTS provider for unit testing."""

    def build_tts(
        self, voice_id: str, language: str = "en", **kwargs: Any
    ) -> Any:
        return self

    async def synthesize(
        self, text: str, voice_id: str | None = None, **kwargs: Any
    ) -> bytes:
        return b"\x00" * 3200


# ── Provider Registries and Factories ──────────────────────────────────

_STT_PROVIDERS: dict[str, type[STTProvider]] = {
    "deepgram": DeepgramSTTProvider,
    "openai": OpenAISTTProvider,
    "mock": MockSTTProvider,
}

_LLM_PROVIDERS: dict[str, type[LLMProvider]] = {
    "google": GoogleLLMProvider,
    "gemini": GoogleLLMProvider,
    "openai": OpenAILLMProvider,
    "mock": MockLLMProvider,
}

_TTS_PROVIDERS: dict[str, type[TTSProvider]] = {
    "elevenlabs": ElevenLabsTTSProvider,
    "cartesia": CartesiaTTSProvider,
    "mock": MockTTSProvider,
}


def register_stt_provider(name: str, provider_cls: type[STTProvider]) -> None:
    _STT_PROVIDERS[name.lower()] = provider_cls


def register_llm_provider(name: str, provider_cls: type[LLMProvider]) -> None:
    _LLM_PROVIDERS[name.lower()] = provider_cls


def register_tts_provider(name: str, provider_cls: type[TTSProvider]) -> None:
    _TTS_PROVIDERS[name.lower()] = provider_cls


def get_stt_provider(name: str | None = None) -> STTProvider:
    provider_name = (name or os.getenv("STT_PROVIDER", "deepgram")).lower()
    cls = _STT_PROVIDERS.get(provider_name, DeepgramSTTProvider)
    return cls()


def get_llm_provider(name: str | None = None) -> LLMProvider:
    provider_name = (name or os.getenv("LLM_PROVIDER", "google")).lower()
    cls = _LLM_PROVIDERS.get(provider_name, GoogleLLMProvider)
    return cls()


def get_tts_provider(name: str | None = None) -> TTSProvider:
    provider_name = (name or os.getenv("TTS_PROVIDER", "elevenlabs")).lower()
    cls = _TTS_PROVIDERS.get(provider_name, ElevenLabsTTSProvider)
    return cls()
