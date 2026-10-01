import asyncio
import logging
import json
import aiohttp
from dotenv import load_dotenv
from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli, TurnHandlingOptions
from livekit.agents.voice.room_io import RoomInputOptions, RoomOptions
from livekit.plugins import google, deepgram, silero, elevenlabs
from livekit.plugins.elevenlabs.tts import VoiceSettings
import os

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "dev-internal-key-change-me")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "qtqlHrXyBpEXHx2JBPgx")

# ── TTS voice (ElevenLabs) ────────────────────────────────────────────────
# Model latency/quality ladder (pick via env):
#   eleven_flash_v2_5      ~75ms   fastest, lowest fidelity
#   eleven_turbo_v2_5      ~250ms  good balance (default)
#   eleven_multilingual_v2 ~500ms+ best fidelity, too slow for live calls
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
# stability/similarity/style/speed should MATCH what you tuned in the ElevenLabs
# dashboard for this voice — otherwise it sounds different from your preview.
ELEVENLABS_STABILITY = float(os.getenv("ELEVENLABS_STABILITY", "0.35"))
ELEVENLABS_SIMILARITY_BOOST = float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.82"))
ELEVENLABS_STYLE = float(os.getenv("ELEVENLABS_STYLE", "0.0"))          # [0.0-1.0] exaggeration
ELEVENLABS_SPEED = float(os.getenv("ELEVENLABS_SPEED", "1.0"))          # [0.8-1.2] talking speed
# speaker_boost lifts voice presence but adds a little latency — turn off for speed.
ELEVENLABS_SPEAKER_BOOST = os.getenv("ELEVENLABS_SPEAKER_BOOST", "true").lower() == "true"

# ── LLM (Gemini) ──────────────────────────────────────────────────────────
# gemini-2.5-flash-lite is faster and has a higher free-tier request cap than
# gemini-2.5-flash (which is 5 req/min free → 429s that break the call).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── Turn-taking (how long to wait after the caller stops before replying) ──
# Lower max_delay = snappier replies; too low can cut the caller off mid-thought.
ENDPOINTING_MIN_DELAY = float(os.getenv("ENDPOINTING_MIN_DELAY", "0.3"))
ENDPOINTING_MAX_DELAY = float(os.getenv("ENDPOINTING_MAX_DELAY", "1.0"))


class MyAgent(Agent):
    def __init__(self, instructions: str):
        super().__init__(instructions=instructions)
        # Transcript is populated by on_user_input / on_agent_reply hooks below
        self.transcript: list[str] = []


async def fetch_campaign(campaign_id: str, contact_id: str | None = None) -> dict | None:
    try:
        headers = {"X-Internal-Key": INTERNAL_API_KEY}
        params = {"contact_id": contact_id} if contact_id else {}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BACKEND_URL}/agent/internal/campaign/{campaign_id}",
                headers=headers,
                params=params,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning(f"Campaign fetch returned {resp.status}")
    except Exception as e:
        logger.error(f"Failed to fetch campaign: {e}")
    return None


async def save_call_log(
    contact_id: str | None,
    campaign_id: str | None,
    transcript: str,
    duration: int,
) -> None:
    # Fix #2: Guard against None IDs — backend would crash on UUID(None)
    if not contact_id or not campaign_id:
        logger.warning(
            "save_call_log: missing contact_id or campaign_id — skipping log save. "
            f"contact_id={contact_id}, campaign_id={campaign_id}"
        )
        return

    try:
        headers = {"X-Internal-Key": INTERNAL_API_KEY}
        async with aiohttp.ClientSession() as session:
            # Fix #2: Properly await the response and check status
            async with session.post(
                f"{BACKEND_URL}/agent/internal/call_log",
                headers=headers,
                json={
                    "contact_id": contact_id,
                    "campaign_id": campaign_id,
                    "status": "completed",
                    "transcript": transcript,
                    "duration": duration,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        f"Call log save returned HTTP {resp.status}: {body}"
                    )
                else:
                    logger.info("Call log saved successfully")
    except Exception as e:
        logger.error(f"Failed to save call log: {e}")


def _build_greeting(language: str, contact_name: str | None, campaign_name: str | None) -> str:
    """Localized opening line — spoken directly via TTS before the LLM is involved,
    so it can't rely on the LLM's language instruction to translate it."""
    templates = {
        "en": ("Hi {name}, I am calling about {campaign}.", "Hi, I am calling about {campaign}."),
        "es": ("Hola {name}, le llamo por {campaign}.", "Hola, le llamo por {campaign}."),
        "fr": ("Bonjour {name}, je vous appelle au sujet de {campaign}.", "Bonjour, je vous appelle au sujet de {campaign}."),
        "hi": ("Namaste {name}, main {campaign} ke baare mein baat karne ke liye call kar raha hoon.", "Namaste, main {campaign} ke baare mein baat karne ke liye call kar raha hoon."),
        "ar": ("مرحباً {name}، أتصل بك بخصوص {campaign}.", "مرحباً، أتصل بك بخصوص {campaign}."),
    }
    with_name, without_name = templates.get(language, templates["en"])
    template = with_name if contact_name else without_name
    return template.format(name=contact_name, campaign=campaign_name)


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room {ctx.room.name}")
    await ctx.connect()

    metadata_str = ""
    for p in ctx.room.remote_participants.values():
        if p.metadata:
            metadata_str = p.metadata
            break

    campaign_id: str | None = None
    contact_id: str | None = None

    if metadata_str:
        # Fix #3: Replace bare `except: pass` with specific exception types
        try:
            metadata = json.loads(metadata_str)
            campaign_id = metadata.get("campaign_id")
            contact_id = metadata.get("contact_id")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse participant metadata: {e}. Raw: {metadata_str!r}")

    # Twilio-originated SIP calls carry no participant metadata (Twilio doesn't
    # set it), so the backend encodes campaign/contact context in the room name
    # instead: "call-<campaign_id>-<contact_id>" (see call_routes.py twilio_twiml).
    # UUIDs are a fixed 36 chars, which lets us slice them out even though they
    # contain hyphens themselves.
    if not campaign_id and ctx.room.name.startswith("call-"):
        remainder = ctx.room.name[len("call-"):]
        UUID_LEN = 36
        if len(remainder) >= UUID_LEN:
            campaign_id = remainder[:UUID_LEN]
            rest = remainder[UUID_LEN:]
            if rest.startswith("-") and len(rest) - 1 >= UUID_LEN:
                contact_id = rest[1:1 + UUID_LEN]
            logger.info(f"Parsed from room name: campaign_id={campaign_id}, contact_id={contact_id}")

    # Appended to every campaign's ai_prompt: this is a live phone call whose
    # text is spoken by TTS verbatim, so markdown (**bold**) and stage
    # directions (*pause*) get read aloud as literal punctuation or bloat
    # reply length, both making speech sound unnatural and slower to start.
    PHONE_CALL_STYLE = (
        "\n\nThis is a live phone call. Reply in plain spoken sentences only: "
        "no markdown, no asterisks, no bullet points, no parenthetical stage "
        "directions. Keep each reply short (1-3 sentences) and conversational.\n\n"
        "Talk like a real person on the phone, not a script: use contractions "
        "(I'm, that's, we'll), start replies with a brief natural acknowledgment "
        "when it fits (Sure, Got it, Okay), and vary your phrasing instead of "
        "reusing the same stock lines. Avoid stiff customer-service phrases like "
        "'That's a good question' or 'I understand your concern.'"
    )

    instructions = "You are a helpful AI assistant. Keep your answers brief." + PHONE_CALL_STYLE
    voice_id = ELEVENLABS_VOICE_ID
    greeting = "Hello, how can I help you today?"
    # These defaults are tuned to sound more natural and less robotic on live
    # phone calls. They can be overridden per campaign/agent or via env vars.
    stability = ELEVENLABS_STABILITY
    similarity_boost = ELEVENLABS_SIMILARITY_BOOST
    language = "en"

    if campaign_id:
        logger.info(f"Fetching campaign config for: {campaign_id}")
        campaign = await fetch_campaign(campaign_id, contact_id)
        if campaign:
            # ai_prompt already has {{variables}} rendered server-side
            # (see /agent/internal/campaign in the backend) using this
            # contact's custom_fields (e.g. doctor_name, appointment_date).
            instructions = campaign.get("ai_prompt", instructions) + PHONE_CALL_STYLE
            if campaign.get("ai_voice"):
                voice_id = campaign.get("ai_voice")

            stability = campaign.get("stability", stability)
            similarity_boost = campaign.get("similarity_boost", similarity_boost)
            language = campaign.get("language") or "en"

            contact_name = (campaign.get("variables") or {}).get("name")
            campaign_name = campaign.get("campaign_name")
            greeting = _build_greeting(language, contact_name, campaign_name)

    # Deepgram's streaming WebSockets API does NOT support Whisper models (returns 405).
    # Deepgram nova-2 and nova-3 both support Arabic ("ar") natively!
    LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "ar": "Arabic", "es": "Spanish", "fr": "French"}
    language_name = LANGUAGE_NAMES.get(language, "English")
    instructions += f"\n\nRespond only in {language_name}, regardless of the language used elsewhere in these instructions."

    stt = deepgram.STT(model="nova-3", language=language)

    my_agent = MyAgent(instructions=instructions)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=stt,
        llm=google.LLM(model=GEMINI_MODEL),
        tts=elevenlabs.TTS(
            model=ELEVENLABS_MODEL,
            voice_id=voice_id,
            api_key=os.getenv("ELEVEN_API_KEY"),
            encoding="pcm_16000",
            language=language,
            # Start speaking as soon as a sentence is ready instead of buffering
            # chunks — cuts time-to-first-audio noticeably on live calls.
            auto_mode=True,
            voice_settings=VoiceSettings(
                stability=stability,
                similarity_boost=similarity_boost,
                style=ELEVENLABS_STYLE,
                speed=ELEVENLABS_SPEED,
                use_speaker_boost=ELEVENLABS_SPEAKER_BOOST,
            ),
        ),
        allow_interruptions=False,
        turn_handling=TurnHandlingOptions(
            endpointing={"mode": "fixed", "min_delay": ENDPOINTING_MIN_DELAY, "max_delay": ENDPOINTING_MAX_DELAY},
            interruption={"enabled": True, "discard_audio_if_uninterruptible": True},
        ),
    )

    # Fix #25: Use get_running_loop() — get_event_loop() is deprecated in Python 3.10+
    start_time = asyncio.get_running_loop().time()

    # Transcript collection. The old user_speech_committed /
    # agent_speech_committed events don't exist in livekit-agents 1.6.x, so
    # those handlers never fired and every call saved an empty transcript.
    # This version emits `conversation_item_added` for each committed turn
    # (including the session.say greeting), carrying a ChatMessage whose
    # .text_content joins all text parts.
    @session.on("conversation_item_added")
    def on_conversation_item(ev):
        role = getattr(ev.item, "role", None)
        text = getattr(ev.item, "text_content", None)
        if role in ("user", "assistant") and text:
            label = "User" if role == "user" else "Agent"
            my_agent.transcript.append(f"{label}: {text}")
#Check end-to-end latency for each call


    room_options = RoomOptions(
        audio_input=RoomInputOptions(),
    )

    await session.start(
        room=ctx.room,
        agent=my_agent,
        room_options=room_options,
    )

    logger.info("Agent joined and is now listening.")
    await session.say(greeting)

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.info(f"Participant disconnected: {participant.identity}")
        # Fix #25: get_running_loop() is the correct async-safe API
        end_time = asyncio.get_running_loop().time()
        duration = int(end_time - start_time)
        full_transcript = "\n".join(my_agent.transcript)
        logger.info(f"Saving call log. Duration={duration}s, transcript_lines={len(my_agent.transcript)}")
        asyncio.create_task(
            save_call_log(contact_id, campaign_id, full_transcript, duration)
        )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))