"""
Campaign execution engine.

Processes campaign contacts sequentially via an ARQ Redis-backed worker task.
Each contact gets: Twilio outbound call → LiveKit room → AI agent conversation.

Fix #5 / #27: Replaced the process-local `_running_campaigns` dict and
`asyncio.create_task` approach with an ARQ job queue.

  - `start_campaign_execution` → enqueues `process_campaign_task` into Redis.
  - `pause_campaign_execution` / `stop_campaign_execution` → write a Redis
    status flag (`campaign:status:{campaign_id}`) that the worker polls.
  - `_process_campaign` → polls the flag on every contact iteration so it
    halts gracefully across process restarts and multiple worker replicas.
"""
import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import arq
import redis.exceptions
from arq.connections import RedisSettings
from fastapi import HTTPException
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_contact import CampaignContact, CampaignContactStatus
from app.models.contact import Contact, ContactStatus
from app.models.call_log import CallLog
from app.services.twilio_service import make_outbound_call

logger = logging.getLogger(__name__)

# Redis key prefix used to signal pause/stop to the ARQ worker.
_CAMPAIGN_STATUS_KEY = "campaign:status:{campaign_id}"


def _parse_redis_settings() -> RedisSettings:
    """Parse REDIS_URL from settings into an ARQ RedisSettings object (handles redis:// and rediss:// with SSL)."""
    return RedisSettings.from_dsn(settings.REDIS_URL)


async def _get_redis_pool() -> arq.ArqRedis:
    """
    Open a short-lived ARQ Redis pool for enqueueing / flag writing.

    ARQ/redis-py already retries transient connection failures internally
    (RedisSettings.conn_retries, default 5). If Redis is still unreachable
    after those retries, translate the raw redis.exceptions error into a
    clean 503 instead of letting it bubble up as an unhandled 500 with a
    stack trace.
    """
    try:
        return await arq.create_pool(_parse_redis_settings())
    except (redis.exceptions.RedisError, OSError) as e:
        logger.error(f"Could not connect to Redis for campaign control: {e}")
        raise HTTPException(
            status_code=503,
            detail="Campaign task queue is temporarily unavailable. Please try again shortly.",
        )


async def recover_orphaned_contacts() -> None:
    """
    Reset any CampaignContacts stuck in CALLING status back to PENDING.
    Should be called at application startup to recover from a crash/restart.
    This prevents contacts from being permanently orphaned after a process restart.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(CampaignContact)
            .where(CampaignContact.call_status == CampaignContactStatus.CALLING)
            .values(call_status=CampaignContactStatus.PENDING)
            .returning(CampaignContact.id)
        )
        recovered = len(result.fetchall())
        if recovered:
            logger.warning(
                f"Startup recovery: reset {recovered} orphaned CALLING contacts → PENDING"
            )
        await db.commit()


async def start_campaign_execution(campaign_id: UUID, business_id: UUID) -> dict:
    """
    Enqueue a campaign processing job into the ARQ Redis worker queue.
    The actual processing happens inside `process_campaign_task` in worker.py.
    """
    redis = await _get_redis_pool()
    try:
        # Clear any stale pause/stop flag from a previous run.
        status_key = _CAMPAIGN_STATUS_KEY.format(campaign_id=str(campaign_id))
        await redis.delete(status_key)

        await redis.enqueue_job(
            "process_campaign_task",
            str(campaign_id),
            str(business_id),
        )
        logger.info(f"Campaign {campaign_id} enqueued into ARQ worker")
    finally:
        await redis.aclose()

    return {"status": "started"}


async def pause_campaign_execution(campaign_id: UUID) -> dict:
    """
    Signal the ARQ worker to pause by setting a Redis flag to 'PAUSED'.
    Also updates the Campaign DB status immediately so the UI reflects the change.
    The worker polls this flag and halts gracefully after the current contact.
    """
    status_key = _CAMPAIGN_STATUS_KEY.format(campaign_id=str(campaign_id))
    redis = await _get_redis_pool()
    try:
        await redis.set(status_key, "PAUSED")
    finally:
        await redis.aclose()

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.PAUSED)
        )
        await db.commit()

    logger.info(f"Campaign {campaign_id} paused via Redis flag")
    return {"status": "paused"}


async def stop_campaign_execution(campaign_id: UUID) -> dict:
    """
    Signal the ARQ worker to cancel by setting a Redis flag to 'CANCELLED'.
    Also updates the Campaign DB status immediately so the UI reflects the change.
    The worker polls this flag and halts gracefully after the current contact.
    """
    status_key = _CAMPAIGN_STATUS_KEY.format(campaign_id=str(campaign_id))
    redis = await _get_redis_pool()
    try:
        await redis.set(status_key, "CANCELLED")
    finally:
        await redis.aclose()

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.CANCELLED)
        )
        await db.commit()

    logger.info(f"Campaign {campaign_id} stopped/cancelled via Redis flag")
    return {"status": "cancelled"}


async def get_campaign_progress(db: AsyncSession, campaign_id: UUID) -> dict | None:
    """Get real-time progress of a campaign."""

    # Get campaign
    campaign = (await db.execute(
        select(Campaign).filter(Campaign.id == campaign_id)
    )).scalar_one_or_none()

    if not campaign:
        return None

    # Fix #6: Replace N+1 per-enum queries with a single GROUP BY query.
    # Previously this ran one SELECT per enum value (5 queries); now it's 1.
    rows = (await db.execute(
        select(CampaignContact.call_status, func.count(CampaignContact.id))
        .where(CampaignContact.campaign_id == campaign_id)
        .group_by(CampaignContact.call_status)
    )).all()

    status_counts: dict[str, int] = {row[0].value: row[1] for row in rows}

    total = sum(status_counts.values())
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    calling = status_counts.get("calling", 0)
    pending = status_counts.get("pending", 0)

    # Derive is_running from the DB campaign status (set by the ARQ worker).
    is_running = campaign.status == CampaignStatus.ACTIVE

    return {
        "campaign_id": str(campaign_id),
        "campaign_status": campaign.status.value if hasattr(campaign.status, "value") else campaign.status,
        "is_running": is_running,
        "total_contacts": total,
        "pending": pending,
        "calling": calling,
        "completed": completed,
        "failed": failed,
        "skipped": status_counts.get("skipped", 0),
        "progress_percent": round((completed + failed) / total * 100, 1) if total > 0 else 0,
    }


# ── Internal: campaign processing loop (called by ARQ worker) ──

async def _process_campaign(ctx: dict, campaign_id: UUID, business_id: UUID) -> None:
    """
    Core campaign loop. Iterates through pending contacts and calls each one.
    Runs as an ARQ background task inside worker.py.

    Pause / stop is coordinated via a Redis flag
    (`campaign:status:{campaign_id}`) that is set by
    `pause_campaign_execution` / `stop_campaign_execution`.
    """
    campaign_key = str(campaign_id)
    status_key = _CAMPAIGN_STATUS_KEY.format(campaign_id=campaign_key)

    # The ARQ worker passes its Redis connection via ctx["redis"].
    redis = ctx.get("redis")

    logger.info(f"Processing campaign {campaign_id}")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(status=CampaignStatus.ACTIVE)
            )
            await db.commit()

        while True:
            # ── Poll the Redis control flag ──
            if redis:
                flag = await redis.get(status_key)
                if flag is not None:
                    flag_str = flag.decode() if isinstance(flag, bytes) else flag
                    if flag_str == "PAUSED":
                        logger.info(f"Campaign {campaign_id} paused by Redis flag")
                        # DB status already updated by pause_campaign_execution.
                        return
                    if flag_str == "CANCELLED":
                        logger.info(f"Campaign {campaign_id} cancelled by Redis flag")
                        # DB status already updated by stop_campaign_execution.
                        return

            # ── Fetch next pending contact ──
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(CampaignContact)
                    .filter(
                        CampaignContact.campaign_id == campaign_id,
                        CampaignContact.call_status == CampaignContactStatus.PENDING,
                    )
                    .order_by(CampaignContact.created_at.asc())
                    .limit(1)
                )
                result = await db.execute(stmt)
                cc = result.scalar_one_or_none()

                if not cc:
                    logger.info(f"Campaign {campaign_id}: no more pending contacts")
                    break

                cc.call_status = CampaignContactStatus.CALLING
                cc.called_at = datetime.now(timezone.utc)
                await db.commit()

                contact_id = cc.contact_id

            await _call_single_contact(campaign_id, business_id, contact_id)

            # Bug #2 fix: Wait for the Twilio status-callback to resolve this
            # contact from CALLING → COMPLETED/FAILED/SKIPPED before moving to
            # the next one. Poll every 3 s, time-out after 5 minutes.
            await _wait_for_contact_resolution(campaign_id, contact_id, timeout_seconds=300)

            # Brief delay between calls (Twilio rate limiting)
            await asyncio.sleep(2)

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(status=CampaignStatus.COMPLETED)
            )
            await db.commit()

        logger.info(f"Campaign {campaign_id} completed successfully")

    except Exception as e:
        logger.error(f"Campaign {campaign_id} failed: {e}", exc_info=True)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(status=CampaignStatus.PAUSED)
            )
            await db.commit()


async def _call_single_contact(
    campaign_id: UUID, business_id: UUID, contact_id: UUID
) -> None:
    """Process a single contact: make outbound call via Twilio."""
    async with AsyncSessionLocal() as db:
        contact = (await db.execute(
            select(Contact).filter(Contact.id == contact_id)
        )).scalar_one_or_none()

        if not contact or not contact.phone_number:
            logger.warning(f"Skipping contact {contact_id}: no phone number")
            await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.SKIPPED)
            return

        # Compliance: never dial a contact who has been marked do-not-call,
        # even if they were added to a campaign before that status was set.
        if contact.status == ContactStatus.DO_NOT_CALL:
            logger.warning(f"Skipping contact {contact_id}: marked as do-not-call")
            await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.SKIPPED)
            return

        try:
            # Fix #8: Twilio SDK uses the synchronous `requests` library under
            # the hood. Running it directly blocks the asyncio event loop for
            # the duration of the HTTP call (200–800ms), freezing all concurrent
            # requests. asyncio.to_thread() offloads it to a thread pool.
            call_result = await asyncio.to_thread(
                make_outbound_call,
                to_number=contact.phone_number,
                campaign_id=str(campaign_id),
                contact_id=str(contact_id),
            )

            if call_result.get("status") == "initiated":
                call_log = CallLog(
                    contact_id=contact_id,
                    campaign_id=campaign_id,
                    business_id=business_id,
                    status="started",
                    call_sid=call_result.get("sid"),
                )
                db.add(call_log)
                await db.commit()

                # Bug #2 fix: Do NOT mark COMPLETED here. The contact stays in
                # CALLING state; the Twilio status-callback webhook
                # (/call/twilio/status-callback) will set COMPLETED or FAILED
                # once the call truly ends (answered+completed, busy, no-answer).
                logger.info(f"Call placed to {contact.phone_number} — SID: {call_result.get('sid')}")

            elif call_result.get("status") == "simulated":
                call_log = CallLog(
                    contact_id=contact_id,
                    campaign_id=campaign_id,
                    business_id=business_id,
                    status="simulated",
                )
                db.add(call_log)
                await db.commit()
                # Simulated calls have no real Twilio callback, so mark done now.
                await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.COMPLETED)
                logger.info(f"Simulated call to {contact.phone_number}")

            else:
                await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)
                logger.warning(f"Call failed to {contact.phone_number}: {call_result}")

        except Exception as e:
            logger.error(f"Error calling contact {contact_id}: {e}", exc_info=True)
            await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)


async def _update_contact_status(
    campaign_id: UUID, contact_id: UUID, status: CampaignContactStatus
) -> None:
    """Update a CampaignContact's call_status."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
            .values(call_status=status)
        )
        await db.commit()


async def _wait_for_contact_resolution(
    campaign_id: UUID,
    contact_id: UUID,
    timeout_seconds: int = 300,
    poll_interval: float = 3.0,
) -> None:
    """
    Bug #2 fix: Poll until the CampaignContact leaves CALLING state.

    When a real Twilio call is placed, the contact stays in CALLING until
    Twilio's status-callback fires and updates it to COMPLETED or FAILED.
    This function blocks the campaign loop from advancing to the next contact
    until that transition happens, OR until timeout_seconds elapses (at which
    point the contact is force-marked FAILED to prevent it being stuck forever).
    """
    _terminal = {
        CampaignContactStatus.COMPLETED,
        CampaignContactStatus.FAILED,
        CampaignContactStatus.SKIPPED,
    }
    elapsed = 0.0
    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(CampaignContact.call_status).where(
                    CampaignContact.campaign_id == campaign_id,
                    CampaignContact.contact_id == contact_id,
                )
            )).scalar_one_or_none()

        if row is None or row in _terminal:
            return  # resolved

        logger.debug(
            f"Waiting for contact {contact_id} to resolve "
            f"(still {row}, elapsed {elapsed:.0f}s)"
        )

    # Timed-out — mark failed so the campaign can continue
    logger.warning(
        f"Contact {contact_id} timed out after {timeout_seconds}s in CALLING state "
        "— marking FAILED (Twilio status-callback may not have arrived)"
    )
    await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)

