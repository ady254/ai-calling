import os
from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Centralized application configuration loaded from environment."""

    # Database
    DATABASE_URL: str = Field(..., description="PostgreSQL async connection string")

    @model_validator(mode="before")
    @classmethod
    def clean_database_url(cls, data: dict) -> dict:
        if isinstance(data, dict) and "DATABASE_URL" in data:
            val = data["DATABASE_URL"]
            if isinstance(val, str):
                # asyncpg does not accept query parameters like sslmode, channel_binding etc.
                # Strip the query string entirely from the URL so SQLAlchemy doesn't pass them as kwargs
                import urllib.parse
                parsed = urllib.parse.urlsplit(val)
                data["DATABASE_URL"] = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return data

    SECRET_KEY: str = Field(..., description="JWT signing secret")
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60)

    # LiveKit
    LIVEKIT_API_KEY: str = Field(..., description="LiveKit API key")
    LIVEKIT_API_SECRET: str = Field(..., description="LiveKit API secret")
    LIVEKIT_URL: str = Field(..., description="LiveKit WebSocket URL")
    # SIP domain shown in LiveKit Console → SIP → Trunks (inbound).
    # e.g. "innvox-um8kvrmw.sip.livekit.cloud"
    # Leave empty to fall back to a recorded greeting instead of SIP.
    LIVEKIT_SIP_DOMAIN: str = Field(default="", description="LiveKit SIP inbound domain for Twilio bridge")

    # LLM
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API key")

    # TTS
    ELEVEN_API_KEY: str = Field(default="", description="ElevenLabs API key")

    # Twilio Voice
    TWILIO_ACCOUNT_SID: str = Field(default="", description="Twilio Account SID")
    TWILIO_AUTH_TOKEN: str = Field(default="", description="Twilio Auth Token")
    TWILIO_PHONE_NUMBER: str = Field(default="", description="Twilio phone number (E.164 format)")

    # Internal API key for agent-to-backend communication.
    # Fix #4: There is NO safe default for this value in any environment.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    INTERNAL_API_KEY: str = Field(
        default="dev-internal-key-change-me",
        description="Shared secret for AI agent worker to call internal endpoints (min 32 chars)"
    )

    # ── Access model ─────────────────────────────────────────────────
    # Access is granted by issuing credentials, not self-serve signup, so the
    # public /auth/signup endpoint is CLOSED BY DEFAULT. Left open, anyone can
    # register, get a Business auto-created, and spend our Twilio/LLM budget.
    # Provision accounts with: python scripts/create_user.py
    # Set true only for local development.
    ALLOW_PUBLIC_SIGNUP: bool = Field(
        default=False,
        description="Allow self-serve registration via POST /auth/signup"
    )

    # CORS
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed CORS origins"
    )

    # Public base URL for Twilio webhooks (e.g., ngrok URL).
    # No env= needed: pydantic-settings maps by field name.
    BASE_URL: str = Field(
        default="http://localhost:8000",
        description="Publicly accessible URL for your backend"
    )

    # Redis Config
    REDIS_URL: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL for arq"
    )

    # ── Observability ────────────────────────────────────────────────
    # Error tracking is opt-in: leave SENTRY_DSN empty and Sentry is a no-op,
    # so local dev / CI never ship events anywhere.
    SENTRY_DSN: str = Field(default="", description="Sentry DSN; error tracking disabled when empty")
    ENVIRONMENT: str = Field(
        default="development",
        description="Deployment environment tag (development | staging | production)"
    )
    SENTRY_TRACES_SAMPLE_RATE: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Fraction of requests traced for performance monitoring (0.0-1.0)"
    )
    LOG_LEVEL: str = Field(default="INFO", description="Root log level (DEBUG|INFO|WARNING|ERROR)")
    LOG_FORMAT: str = Field(
        default="text",
        description="'json' for structured logs (use in deployed envs), 'text' for human-readable local dev"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_internal_key(self) -> 'Settings':
        # Fix #4: The old check only rejected the default in non-localhost envs,
        # meaning a misconfigured BASE_URL could silently bypass it.
        # Now we: (a) always reject the known-bad default, (b) require minimum
        # 32 character length to prevent trivially guessable keys.
        if self.INTERNAL_API_KEY == "dev-internal-key-change-me":
            raise ValueError(
                "Security Risk: INTERNAL_API_KEY is set to the default placeholder value. "
                "Generate a secure key with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(self.INTERNAL_API_KEY) < 32:
            raise ValueError(
                f"Security Risk: INTERNAL_API_KEY is too short ({len(self.INTERNAL_API_KEY)} chars). "
                "Minimum required length is 32 characters."
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]


settings = Settings()
