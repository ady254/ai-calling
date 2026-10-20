"""
External service provider abstractions and concrete implementations.
"""
from app.providers.base import (
    LLMProvider,
    STTProvider,
    TTSProvider,
    TelephonyProvider,
)
from app.providers.llm import (
    GeminiLLMProvider,
    MockLLMProvider,
    OpenAILLMProvider,
    get_llm_provider,
    set_default_llm_provider,
)
from app.providers.telephony import (
    MockTelephonyProvider,
    TelnextProvider,
    TelnyxProvider,
    TwilioProvider,
    get_telephony_provider,
    set_default_telephony_provider,
)

__all__ = [
    "TelephonyProvider",
    "LLMProvider",
    "STTProvider",
    "TTSProvider",
    "TwilioProvider",
    "TelnyxProvider",
    "TelnextProvider",
    "MockTelephonyProvider",
    "get_telephony_provider",
    "set_default_telephony_provider",
    "GeminiLLMProvider",
    "OpenAILLMProvider",
    "MockLLMProvider",
    "get_llm_provider",
    "set_default_llm_provider",
]
