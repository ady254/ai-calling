import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.auth_routes import router as auth_router
from app.api.business_routes import router as business_router
from app.api.campaign_routes import router as campaign_router
from app.api.contact_routes import router as contact_router
from app.api.call_routes import router as call_router
from app.api.livekit_routes import router as livekit_router
from app.api.agent_routes import router as agent_router

from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.observability import init_sentry, request_id_var, setup_logging
from app.services.campaign_executor import recover_orphaned_contacts

# Configure logging + error tracking before anything else emits a log line.
# LOG_FORMAT=json gives structured, queryable logs in deployed environments;
# Sentry is a no-op unless SENTRY_DSN is set.
setup_logging()
init_sentry()

logger = logging.getLogger(__name__)


# Fix #11: Replace deprecated @app.on_event("startup") with lifespan context manager.
# Fix #12: Removed `Base.metadata.create_all` from startup.
#   → Use `alembic upgrade head` in your Dockerfile/entrypoint to apply migrations.
#   → create_all only creates missing tables; it does NOT apply column changes,
#     leading to silent schema drift in production.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Bug #1 fix: Warn loudly at startup if webhook URL can't be reached by Twilio ──
    base_url = settings.BASE_URL
    if not base_url or "localhost" in base_url or "127.0.0.1" in base_url:
        logger.warning(
            "\u26a0\ufe0f  BASE_URL is set to a local address (%s). "
            "Twilio cannot reach this URL for webhooks. "
            "Run ngrok and set BASE_URL to the public HTTPS URL in .env, then restart.",
            base_url,
        )
    else:
        logger.info("BASE_URL (Twilio webhook base): %s", base_url)

    if not settings.LIVEKIT_SIP_DOMAIN:
        logger.warning(
            "\u26a0\ufe0f  LIVEKIT_SIP_DOMAIN is not set. "
            "Calls will play a static greeting instead of connecting to the AI agent. "
            "Set LIVEKIT_SIP_DOMAIN in .env (found in LiveKit Console \u2192 SIP \u2192 Trunks)."
        )

    # Startup: recover any contacts orphaned in CALLING state by a prior crash
    logger.info("Running startup recovery for orphaned campaign contacts...")
    await recover_orphaned_contacts()
    logger.info("Startup complete.")
    yield
    # Shutdown (add cleanup here if needed, e.g. close connection pools)


from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

app = FastAPI(
    title="AI Calling Platform API",
    description="Backend API for V3 AI Voice Calling Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# Trust reverse-proxy headers from Caddy (X-Forwarded-Proto: https, X-Forwarded-For)
# so FastAPI knows the request is HTTPS and generates https:// URLs/redirects.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/audio", StaticFiles(directory="audio"), name="audio")

# Fix #23: Tighten CORS — restrict methods and headers instead of using wildcards.
# Origins are already pulled from config (settings.cors_origins).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-Key", "X-Request-ID"],
    # Let the browser read the correlation id so support can quote it.
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Tag every request with a correlation id.

    Each log line emitted while handling the request carries this id, and it's
    echoed back on the response — so a user-reported failure maps to exactly
    the right log lines. Honours an inbound X-Request-ID if a proxy set one.
    """
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        request_id_var.reset(token)

app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(business_router, prefix="/business", tags=["Business"])
app.include_router(campaign_router, prefix="/campaign", tags=["Campaign"])
app.include_router(contact_router, prefix="/contact", tags=["Contact"])
app.include_router(call_router, prefix="/call", tags=["Calls"])
app.include_router(livekit_router, prefix="/livekit", tags=["LiveKit"])
app.include_router(agent_router, prefix="/agent", tags=["Agent"])


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for Docker/monitoring."""
    return {"status": "healthy", "service": "ai-calling-backend"}
