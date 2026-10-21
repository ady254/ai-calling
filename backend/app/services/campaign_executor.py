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
from typing import Any
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
from app.providers import TelephonyProvider, get_telephony_provider
from app.services.twilio_service import make_outbound_call

logger = logging.getLogger(__name__)

# Redis key prefix used to signal pause/stop to the ARQ worker.
_CAMPAIGN_STATUS_KEY = "campaign:status:{campaign_id}"
_RESOLUTION_CHANNEL = "campaign:resolution:{campaign_id}:{contact_id}"


async def _notify_contact_resolution(campaign_id: UUID, contact_id: UUID, status: str) -> None:
    """Publish resolution event to Redis Pub/Sub to wake waiting workers immediately without DB polling."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL)
        channel = _RESOLUTION_CHANNEL.format(campaign_id=str(campaign_id), contact_id=str(contact_id))
        await client.publish(channel, status)
        await client.aclose()
    except Exception as e:
        logger.debug(f"Could not publish resolution to Redis: {e}")


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


# ── Single-responsibility sub-routines for campaign orchestration ────

async def _check_control_flag(redis: Any, campaign_id: UUID) -> str | None:
    """
    Poll the Redis control flag for PAUSED or CANCELLED signals.
    Returns 'PAUSED', 'CANCELLED', or None if running normally.
    """
    if not redis:
        return None

    status_key = _CAMPAIGN_STATUS_KEY.format(campaign_id=str(campaign_id))
    flag = await redis.get(status_key)
    if flag is not None:
        flag_str = flag.decode() if isinstance(flag, bytes) else flag
        if flag_str in ("PAUSED", "CANCELLED"):
            logger.info(f"Campaign {campaign_id} {flag_str.lower()} by Redis flag")
            return flag_str
    return None


async def _fetch_all_pending(campaign_id: UUID) -> list[UUID]:
    """
    Fetch all pending contact IDs for this campaign in FIFO order.
    Returns empty list if no pending contacts remain.
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(CampaignContact.contact_id)
            .filter(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.call_status == CampaignContactStatus.PENDING,
            )
            .order_by(CampaignContact.created_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def _fetch_next_pending_contact(campaign_id: UUID) -> UUID | None:
    """
    Fetch the next pending contact ID for this campaign in FIFO order.
    Returns None if no pending contacts remain.
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(CampaignContact.contact_id)
            .filter(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.call_status == CampaignContactStatus.PENDING,
            )
            .order_by(CampaignContact.created_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def _mark_calling(campaign_id: UUID, contact_id: UUID) -> None:
    """Update contact state to CALLING and record dial timestamp."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
            .values(
                call_status=CampaignContactStatus.CALLING,
                called_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def _finalize_campaign(campaign_id: UUID, status: CampaignStatus) -> None:
    """Update campaign terminal or pause status."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=status)
        )
        await db.commit()
    logger.info(f"Campaign {campaign_id} status updated to {status.value if hasattr(status, 'value') else status}")


# ── Internal: campaign processing orchestrator (called by ARQ worker) ──

MAX_CONCURRENT = 5  # Twilio rate limit safe zone for bounded concurrency


async def _process_campaign(
    ctx: dict,
    campaign_id: UUID,
    business_id: UUID,
    telephony_provider: TelephonyProvider | None = None,
) -> None:
    """
    Core campaign loop orchestrator with bounded concurrency (MAX_CONCURRENT=5).
    Replaces slow sequential execution with concurrent worker tasks bounded by a Semaphore,
    and event-driven call resolution via Redis Pub/Sub.
    """
    redis = ctx.get("redis")
    logger.info(f"Processing campaign {campaign_id} with concurrency limit {MAX_CONCURRENT}")

    try:
        await _finalize_campaign(campaign_id, CampaignStatus.ACTIVE)

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def _call_with_semaphore(contact_id: UUID) -> None:
            # Check control flag before acquiring semaphore slot
            flag = await _check_control_flag(redis, campaign_id)
            if flag in ("PAUSED", "CANCELLED"):
                return

            async with semaphore:
                # Re-check control flag once semaphore is acquired
                flag = await _check_control_flag(redis, campaign_id)
                if flag in ("PAUSED", "CANCELLED"):
                    return

                await _mark_calling(campaign_id, contact_id)
                await _call_single_contact(
                    campaign_id,
                    business_id,
                    contact_id,
                    telephony_provider=telephony_provider,
                )
                await _wait_for_contact_resolution(campaign_id, contact_id, timeout_seconds=300)

        # Fetch pending contacts and process concurrently up to MAX_CONCURRENT
        contacts = await _fetch_all_pending(campaign_id)
        if contacts:
            await asyncio.gather(*[_call_with_semaphore(cid) for cid in contacts])

        # Check if paused/cancelled mid-campaign
        flag = await _check_control_flag(redis, campaign_id)
        if flag not in ("PAUSED", "CANCELLED"):
            await _finalize_campaign(campaign_id, CampaignStatus.COMPLETED)
            logger.info(f"Campaign {campaign_id} completed successfully")

    except Exception as e:
        logger.error(f"Campaign {campaign_id} failed: {e}", exc_info=True)
        await _finalize_campaign(campaign_id, CampaignStatus.PAUSED)


async def _call_single_contact(
    campaign_id: UUID,
    business_id: UUID,
    contact_id: UUID,
    telephony_provider: TelephonyProvider | None = None,
) -> None:
    """Process a single contact: make outbound call via TelephonyProvider."""
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
            # Fix #8: Telephony SDK uses synchronous requests under the hood.
            # Running it directly blocks the event loop; asyncio.to_thread offloads it.
            provider = telephony_provider or get_telephony_provider()
            call_result = await asyncio.to_thread(
                provider.make_call,
                to=contact.phone_number,
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
                # Notify any listeners immediately
                await _notify_contact_resolution(campaign_id, contact_id, "completed")
                logger.info(f"Simulated call to {contact.phone_number}")

            else:
                await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)
                await _notify_contact_resolution(campaign_id, contact_id, "failed")
                logger.warning(f"Call failed to {contact.phone_number}: {call_result}")

        except Exception as e:
            logger.error(f"Error calling contact {contact_id}: {e}", exc_info=True)
            await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)
            await _notify_contact_resolution(campaign_id, contact_id, "failed")


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
) -> None:
    """
    Event-driven call resolution via Redis Pub/Sub (B2 Problem 3 fix).
    Subscribes to Redis Pub/Sub channel 'campaign:resolution:{campaign_id}:{contact_id}'
    so the worker wakes up IMMEDIATELY when Twilio's status-callback fires,
    completely eliminating repeated DB SELECT queries while waiting.
    Falls back gracefully if Redis is unavailable or on timeout.
    """
    _terminal = {
        CampaignContactStatus.COMPLETED,
        CampaignContactStatus.FAILED,
        CampaignContactStatus.SKIPPED,
    }

    # Initial check: call might already have resolved (e.g. simulated call or instant error)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(CampaignContact.call_status).where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
        )).scalar_one_or_none()

    if row is None or row in _terminal:
        return

    # Subscribe to Redis Pub/Sub channel for instant event wakeup
    channel_name = _RESOLUTION_CHANNEL.format(campaign_id=str(campaign_id), contact_id=str(contact_id))
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel_name)

        try:
            # Wait for event notification up to timeout_seconds
            async with asyncio.timeout(timeout_seconds):
                async for message in pubsub.listen():
                    if message and message.get("type") == "message":
                        # Received resolution notification!
                        return
        except asyncio.TimeoutError:
            logger.warning(
                f"Contact {contact_id} timed out after {timeout_seconds}s waiting for Pub/Sub resolution "
                "— marking FAILED"
            )
        finally:
            try:
                await pubsub.unsubscribe(channel_name)
                await pubsub.close()
                await client.aclose()
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"Redis Pub/Sub resolution fallback to sleep for {contact_id}: {e}")
        # Graceful fallback: one single sleep up to timeout
        await asyncio.sleep(min(timeout_seconds, 10))

    # Final check / timeout enforcement: ensure status is updated if not yet resolved
    async with AsyncSessionLocal() as db:
        final_row = (await db.execute(
            select(CampaignContact.call_status).where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
        )).scalar_one_or_none()

    if final_row not in _terminal:
        await _update_contact_status(campaign_id, contact_id, CampaignContactStatus.FAILED)

