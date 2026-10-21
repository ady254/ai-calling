"""
Rate limiting configuration using SlowAPI.

Applies per-IP rate limits to protect against brute-force and abuse.
Auth endpoints get stricter limits than general API endpoints.
"""
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings

# Create the limiter instance keyed by client IP, backed by Redis so rate
# limit counts are shared across all worker processes / replicas.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
)
