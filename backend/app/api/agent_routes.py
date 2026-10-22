import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from uuid import UUID
from typing import List, Optional
import io
import secrets
# Fix #22: Replace silent `except ImportError: pass` with a proper sentinel.
# If elevenlabs is not installed, ElevenLabs is None and the /preview endpoint
# returns a clean 503 instead of crashing with a NameError at call time.
try:
    from elevenlabs.client import ElevenLabs as _ElevenLabsClient
except ImportError:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "elevenlabs package is not installed — voice preview endpoint will be unavailable. "
        "Install it with: pip install elevenlabs"
    )
    _ElevenLabsClient = None  # type: ignore[assignment,misc]

from app.schemas.agent_schema import AgentCreate, AgentUpdate, AgentOut
from app.services.agent_service import (
    create_agent, get_agents_by_business, get_agent,
    update_agent, delete_agent
)
from app.models.business import Business
from app.models.campaign import Campaign
from app.models.agent import Agent
from app.models.call_log import CallLog
from app.models.contact import Contact
from app.schemas.call_schema import CallLogCreate
from app.dependencies.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.business import get_user_business
from app.dependencies.ownership import get_owned_agent
from app.core.config import settings
from app.utils.templating import render_template
from app.core.rate_limit import limiter
from fastapi import Header

router = APIRouter()

logger = logging.getLogger(__name__)

@router.post("/", response_model=AgentOut)
async def create_agent_route(
    data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    data.business_id = business.id
    return await create_agent(db, data)

@router.get("/", response_model=List[AgentOut])
async def list_agents_route(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    return await get_agents_by_business(db, business.id, skip, limit)

@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent_route(
    agent: Agent = Depends(get_owned_agent),
):
    return agent

@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent_route(
    agent_id: UUID,
    data: AgentUpdate,
    agent: Agent = Depends(get_owned_agent),
    db: AsyncSession = Depends(get_db),
):
    return await update_agent(db, agent_id, data)

@router.delete("/{agent_id}")
async def delete_agent_route(
    agent_id: UUID,
    agent: Agent = Depends(get_owned_agent),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_agent(db, agent_id)
    return {"success": success}

@router.post("/preview")
@limiter.limit("10/minute")
async def preview_agent_voice(
    request: Request,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    business = await get_user_business(db, user_id)
    
    voice_id = data.get("voice_id")
    text = data.get("text", "Hi! I am your AI agent. How can I help you today?")
    if not voice_id:
        raise HTTPException(status_code=400, detail="Voice ID is required")
        
    # Fix #22: Guard against the package not being installed.
    if _ElevenLabsClient is None:
        raise HTTPException(
            status_code=503,
            detail="Voice preview is unavailable: the ElevenLabs package is not installed on this server.",
        )

    if not settings.ELEVEN_API_KEY:
        raise HTTPException(status_code=500, detail="ElevenLabs API Key is not configured")

    try:
        client = _ElevenLabsClient(api_key=settings.ELEVEN_API_KEY)
        audio = client.generate(
            text=text,
            voice=voice_id,
            model="eleven_multilingual_v2"
        )
        audio_data = b"".join(audio)
        return StreamingResponse(io.BytesIO(audio_data), media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Internal Routes (for AI Agent Worker) ---

# Fix #4: Require the header to be present and check its length.
# Uses secrets.compare_digest instead of `!=` so the comparison takes
# constant time regardless of where the strings first differ, closing a
# timing side-channel an attacker could otherwise use to guess the key
# byte-by-byte.
async def verify_internal_key(x_internal_key: str = Header(...)):
    if (
        not x_internal_key
        or len(x_internal_key) < 32
        or not secrets.compare_digest(x_internal_key, settings.INTERNAL_API_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid internal API key")

@router.get("/internal/campaign/{campaign_id}", tags=["Internal"])
async def get_campaign_config_internal(
    campaign_id: UUID,
    contact_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_internal_key)
):
    """
    Returns the full configuration for an AI call,
    merging Campaign and Agent Persona settings.

    If contact_id is supplied, the contact's name/phone/custom_fields are
    rendered into {{variable}} placeholders in ai_prompt so every call is
    personalized (e.g. "Hi {{name}}, this is a reminder about your
    appointment with {{doctor_name}} on {{appointment_date}}.").
    """
    # Fetch campaign
    stmt = select(Campaign).filter(Campaign.id == campaign_id)
    result = await db.execute(stmt)
    campaign = result.scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    config = {
        "campaign_name": campaign.name,
        "ai_prompt": campaign.ai_prompt,
        "ai_voice": campaign.ai_voice,
        "language": campaign.language,
        "max_retries": campaign.max_retries,
    }

    # If there's a linked agent, override with agent settings
    if campaign.agent_id:
        agent = await get_agent(db, campaign.agent_id)
        if agent:
            config["ai_prompt"] = agent.system_prompt
            config["ai_voice"] = agent.voice_id
            config["language"] = agent.language
            config["stability"] = agent.stability
            config["similarity_boost"] = agent.similarity_boost

    variables: dict = {}
    if contact_id:
        contact = (await db.execute(
            select(Contact).filter(
                Contact.id == contact_id, Contact.business_id == campaign.business_id
            )
        )).scalar_one_or_none()
        if contact:
            variables = {
                "name": contact.name,
                "phone_number": contact.phone_number,
                "email": contact.email,
                "company": contact.company,
                **(contact.custom_fields or {}),
            }
            config["ai_prompt"] = render_template(config["ai_prompt"], variables)

    config["variables"] = variables
    return config

async def _extract_and_store_outcome(call_log_id: UUID, transcript: str) -> None:
    """
    Background task: run LLM outcome extraction on a finished call's
    transcript and write the result onto the CallLog row. Runs after the
    response is sent, with its own DB session — a failure here (e.g. a
    Gemini rate limit) leaves outcome null but never loses the call log.
    """
    from app.db.session import AsyncSessionLocal
    from app.services.ai_service import extract_call_outcome

    result = await extract_call_outcome(transcript)
    if not result:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(CallLog).where(CallLog.id == call_log_id).values(**result)
        )
        await db.commit()
    logger.info(f"Call log {call_log_id} outcome extracted: {result.get('outcome')}")


@router.post("/internal/call_log", tags=["Internal"])
async def create_call_log_internal(
    data: dict,  # Using dict because we need both contact and campaign ID
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_internal_key)
):
    """
    Saves a call log from the AI worker, then enqueues outcome extraction
    into the durable ARQ worker queue with automatic retries.
    """
    campaign_id = UUID(data.get("campaign_id"))
    contact_id = UUID(data.get("contact_id"))

    # Get campaign to find business_id
    stmt = select(Campaign).filter(Campaign.id == campaign_id)
    result = await db.execute(stmt)
    campaign = result.scalar_one_or_none()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    call_log = CallLog(
        contact_id=contact_id,
        campaign_id=campaign_id,
        business_id=campaign.business_id,
        status=data.get("status", "completed"),
        transcript=data.get("transcript"),
        duration=data.get("duration", 0)
    )

    db.add(call_log)
    await db.commit()

    if call_log.transcript:
        try:
            from app.services.campaign_executor import _get_redis_pool
            redis_pool = await _get_redis_pool()
            await redis_pool.enqueue_job(
                "extract_outcome_task", str(call_log.id), call_log.transcript
            )
        except Exception as e:
            logger.error(
                f"Failed to enqueue extract_outcome_task for log {call_log.id}: {e}"
            )

    return {"status": "success"}