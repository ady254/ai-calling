import os
import uuid
import time
import glob
import logging
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from app.core.config import settings

load_dotenv()
logger = logging.getLogger(__name__)

_client: ElevenLabs | None = None


def _get_elevenlabs_client() -> ElevenLabs:
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=settings.ELEVEN_API_KEY)
    return _client

# Use a real ElevenLabs voice ID — "Bella" voice
VOICE_ID = "ibbx9zDYGvLgtYzRbqqG"
MODEL_ID = "eleven_multilingual_v2"


def text_to_speech(text: str) -> str:
    """Convert text to speech using ElevenLabs and save to a unique file."""

    os.makedirs("audio", exist_ok=True)

    # Cleanup old audio files (older than 5 minutes)
    _cleanup_old_audio()

    # Generate unique filename to prevent caching/collision
    filename = f"{uuid.uuid4().hex}.mp3"
    file_path = f"audio/{filename}"

    try:
        # Generate audio via ElevenLabs
        client = _get_elevenlabs_client()
        audio_generator = client.text_to_speech.convert(
            text=text,
            voice_id=VOICE_ID,
            model_id=MODEL_ID,
        )

        # Write streamed audio chunks to file
        with open(file_path, "wb") as f:
            for chunk in audio_generator:
                if chunk:
                    f.write(chunk)

        logger.info(f"Generated audio: {file_path}")
    except Exception as e:
        logger.error(f"ElevenLabs error: {e}")
        logger.info("Falling back to gTTS...")
        from gtts import gTTS
        tts = gTTS(text=text, lang="en")
        tts.save(file_path)

    return file_path


def _cleanup_old_audio(max_age_seconds: int = 300):
    """Remove audio files older than max_age_seconds (default 5 min)."""
    now = time.time()
    for filepath in glob.glob("audio/*.mp3"):
        try:
            if now - os.path.getmtime(filepath) > max_age_seconds:
                os.remove(filepath)
        except OSError:
            pass