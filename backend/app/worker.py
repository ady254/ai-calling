"""
ARQ background worker.

Fix #5 / #27: Implements `process_campaign_task` which is enqueued by
`start_campaign_execution` in campaign_executor.py.  The worker picks it up
from Redis, calls the core `_process_campaign` loop, and gracefully honours
pause/stop flags written to Redis by `pause_campaign_execution` /
`stop_campaign_execution`.

Run the worker with:
    arq app.worker.WorkerSettings
"""
import logging
from uuid import UUID

from arq.connections import RedisSettings

from app.core.config import settings
# Import every model so SQLAlchemy's declarative registry is fully populated
# before any mapper is used. Without this, this standalone process (unlike
# app.main, which transitively imports every model via its routers) only
# pulls in Campaign/Contact/CampaignContact/CallLog through
# campaign_executor.py, and crashes the first job with "expression
# 'Business' failed to locate a name" since Business is never imported here.
import app.models  # noqa: F401
from app.core.observability import init_sentry, setup_logging

# Same logging/error-tracking setup as the API. Without this, a campaign job
# that raises here fails silently in a detached worker — nobody finds out until
# a customer reports their campaign never ran.
setup_logging()
init_sentry()

logger = logging.getLogger("worker")


def parse_redis_url(url: str) -> RedisSettings:
    """Parse a redis:// or rediss:// URL into an ARQ RedisSettings object with SSL support."""
    return RedisSettings.from_dsn(url)


async def startup(ctx: dict) -> None:
    logger.info("Worker starting up...")


async def shutdown(ctx: dict) -> None:
    logger.info("Worker shutting down...")


async def process_campaign_task(ctx: dict, campaign_id: str, business_id: str) -> None:
    """
    ARQ task: run the campaign processing loop for one campaign.

    `ctx` is provided by ARQ and contains (among other things) `ctx['redis']`,
    which `_process_campaign` uses to poll the pause/stop control flag.

    Args:
        ctx:         ARQ worker context (injected automatically).
        campaign_id: String UUID of the campaign to process.
        business_id: String UUID of the owning business.
    """
    from app.services.campaign_executor import _process_campaign  # local import avoids circularity

    logger.info(f"Worker picked up campaign {campaign_id}")
    await _process_campaign(ctx, UUID(campaign_id), UUID(business_id))
    logger.info(f"Worker finished campaign {campaign_id}")


async def extract_outcome_task(ctx: dict, log_id: str, transcript: str) -> None:
    """
    ARQ task: extract structured call outcome from transcript and update CallLog.
    Supports per-agent custom prompt_template if defined on the associated agent.
    Durable and retried automatically by ARQ with exponential backoff on failure.
    """
    from sqlalchemy import select, update
    from app.db.session import AsyncSessionLocal
    from app.models.call_log import CallLog
    from app.models.campaign import Campaign
    from app.models.agent import Agent
    from app.services.ai_service import extract_call_outcome

    logger.info(f"Worker processing outcome extraction for call_log {log_id}")

    prompt_template = None
    async with AsyncSessionLocal() as db:
        # Check if the call is tied to a campaign with an agent that has a custom prompt_template
        log_row = (await db.execute(
            select(CallLog.campaign_id).where(CallLog.id == UUID(log_id))
        )).scalar_one_or_none()

        if log_row:
            agent_row = (await db.execute(
                select(Agent.prompt_template)
                .join(Campaign, Campaign.agent_id == Agent.id)
                .where(Campaign.id == log_row)
            )).scalar_one_or_none()
            if agent_row:
                prompt_template = agent_row

    result = await extract_call_outcome(transcript, prompt_template=prompt_template)
    if result:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(CallLog)
                .where(CallLog.id == UUID(log_id))
                .values(**result)
            )
            await db.commit()
        logger.info(f"Call log {log_id} outcome extracted: {result.get('outcome')}")
    else:
        logger.warning(f"Outcome extraction yielded no result for call_log {log_id}")


class WorkerSettings:
    functions = [process_campaign_task, extract_outcome_task]
    redis_settings = parse_redis_url(settings.REDIS_URL)
    max_tries = 3
    on_startup = startup
    on_shutdown = shutdown
