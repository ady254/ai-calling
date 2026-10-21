"""
Tests for rate limiting configuration and Redis backing.
"""
from app.core.config import settings
from app.core.rate_limit import limiter


def test_rate_limiter_uses_redis_storage():
    """Verify rate limiter is configured with settings.REDIS_URL to share state across workers."""
    assert limiter._storage_uri == settings.REDIS_URL
