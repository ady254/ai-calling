import json
import logging
from typing import Any

from app.providers import LLMProvider, get_llm_provider

logger = logging.getLogger(__name__)


# ── Post-call outcome extraction ──────────────────────────────────────
# The live phone conversation is handled by the LiveKit agent (see ai-agent/),
# not here. This module's job is analysing a finished transcript.
#
# Migrated (SOLID refactor): The hard-coded import-time `genai.Client` singleton
# was replaced with an injectable `LLMProvider` interface. Model creation is lazy,
# supports multiple LLM providers (Gemini, OpenAI, Mock), and allows clean dependency
# injection without monkey-patching.

async def _generate_json(prompt: str, provider: LLMProvider | None = None) -> str:
    """Send `prompt` to the LLM provider and return its raw JSON text."""
    active_provider = provider or get_llm_provider()
    return await active_provider.complete(prompt, response_mime_type="application/json")


# Allowed outcome values — anything else from the LLM is coerced to "other"
# so the DB column stays a clean, filterable enum-like set.
CALL_OUTCOMES = {
    "confirmed",
    "rescheduled",
    "callback_requested",
    "not_interested",
    "do_not_call",
    "wrong_person",
    "incomplete",
    "other",
}

_EXTRACTION_PROMPT = """You are analyzing the transcript of an outbound AI phone call made on behalf of a business. Lines are prefixed "Agent:" (the AI caller) and "User:" (the person who was called).

Return a JSON object with exactly these keys:
- "outcome": one of "confirmed" | "rescheduled" | "callback_requested" | "not_interested" | "do_not_call" | "wrong_person" | "incomplete" | "other"
    confirmed: the person agreed to or confirmed the purpose of the call (e.g. confirmed an appointment)
    rescheduled: they asked to move it to a different date or time
    callback_requested: they asked to be called back later
    not_interested: they declined the offer or purpose of the call
    do_not_call: they asked not to be contacted again
    wrong_person: wrong number, or the intended person was not available
    incomplete: the call ended before the purpose was addressed
    other: none of the above fits
- "summary": 1-2 plain sentences describing what happened on the call
- "follow_up": the specific next action the business must take, including any date/time the person mentioned, or null if no action is needed

Transcript:
{transcript}"""


async def extract_call_outcome(
    transcript: str,
    llm_provider: LLMProvider | None = None,
    prompt_template: str | None = None,
) -> dict[str, Any] | None:
    """
    Analyze a finished call transcript and return
    {"outcome": ..., "summary": ..., "follow_up": ...} or None on failure.

    Supports custom per-agent/client `prompt_template` (Worst Offender 3 fix).
    Supports dependency injection via `llm_provider`. If None, uses default provider.
    """
    if not transcript or len(transcript.strip()) < 20:
        return None

    try:
        template = prompt_template if (prompt_template and "{transcript}" in prompt_template) else _EXTRACTION_PROMPT
        formatted_prompt = template.format(transcript=transcript)
        if llm_provider is not None:
            raw = await llm_provider.complete(formatted_prompt, response_mime_type="application/json")
        else:
            raw = await _generate_json(formatted_prompt)

        data = json.loads(raw)

        outcome = data.get("outcome")
        if outcome not in CALL_OUTCOMES:
            outcome = "other"

        summary = (data.get("summary") or "").strip()[:1000] or None
        follow_up = (data.get("follow_up") or "").strip()[:1000] or None
        # json.loads can yield the string "null" text if the model misbehaves
        if follow_up and follow_up.lower() == "null":
            follow_up = None

        return {"outcome": outcome, "summary": summary, "follow_up": follow_up}

    except Exception as e:
        logger.error(f"Call outcome extraction failed: {e}")
        return None