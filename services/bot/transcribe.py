"""
Atlas Bot — Voice Transcription Module
---------------------------------------
Dual-engine STT pipeline for Telegram voice notes.

Primary:  Groq Whisper API (whisper-large-v3-turbo) — ~0.3s, cloud-based
Fallback: faster-whisper (base model, local VPS) — ~2-3s, no external dependency

Both engines receive raw OGG bytes from Telegram and return a plain text transcript.
Audio conversion (OGG → WAV) is handled here via pydub/ffmpeg.
"""

import io
import logging
import os
import tempfile

logger = logging.getLogger("atlas.bot.transcribe")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
WHISPER_FALLBACK_MODEL = os.getenv("WHISPER_FALLBACK_MODEL", "base")


def _ogg_to_wav(ogg_bytes: bytes) -> bytes:
    """Convert OGG/Opus bytes (Telegram format) to WAV bytes using pydub."""
    from pydub import AudioSegment
    audio = AudioSegment.from_ogg(io.BytesIO(ogg_bytes))
    wav_buffer = io.BytesIO()
    audio.export(wav_buffer, format="wav")
    return wav_buffer.getvalue()


async def _transcribe_groq(wav_bytes: bytes) -> str:
    """Transcribe audio using Groq Whisper API. Returns transcript string."""
    from groq import AsyncGroq
    client = AsyncGroq(api_key=GROQ_API_KEY)

    # Groq requires a file-like object with a name attribute
    wav_file = io.BytesIO(wav_bytes)
    wav_file.name = "audio.wav"

    transcription = await client.audio.transcriptions.create(
        file=wav_file,
        model="whisper-large-v3-turbo",
        response_format="text",
        language="en",
    )
    return transcription.strip()


def _transcribe_faster_whisper(wav_bytes: bytes) -> str:
    """Transcribe audio using local faster-whisper. Returns transcript string."""
    from faster_whisper import WhisperModel

    logger.info("Groq unavailable — falling back to local faster-whisper (%s).", WHISPER_FALLBACK_MODEL)
    model = WhisperModel(WHISPER_FALLBACK_MODEL, device="cpu", compute_type="int8")

    # Write wav to a temp file since faster-whisper needs a file path
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name

    segments, _ = model.transcribe(tmp_path, beam_size=5)
    transcript = " ".join(seg.text for seg in segments).strip()

    import os as _os
    _os.unlink(tmp_path)
    return transcript


async def transcribe_audio(ogg_bytes: bytes) -> str:
    """
    Main entry point. Converts OGG → WAV, tries Groq first, falls back to
    faster-whisper if Groq fails for any reason.

    Args:
        ogg_bytes: Raw OGG audio bytes downloaded from Telegram.

    Returns:
        Plain text transcript string.

    Raises:
        RuntimeError: If both engines fail.
    """
    logger.info("Converting OGG → WAV for transcription (%.1f KB).", len(ogg_bytes) / 1024)
    try:
        wav_bytes = _ogg_to_wav(ogg_bytes)
    except Exception as e:
        raise RuntimeError(f"Failed to convert audio format: {e}") from e

    # ── Primary: Groq ──────────────────────────────────────────────────────────
    if GROQ_API_KEY:
        try:
            logger.info("Attempting Groq Whisper transcription...")
            transcript = await _transcribe_groq(wav_bytes)
            logger.info("Groq transcription successful: %r", transcript[:80])
            return transcript
        except Exception as e:
            logger.warning("Groq transcription failed (%s). Trying fallback...", e)
    else:
        logger.warning("GROQ_API_KEY not set — skipping Groq, using local fallback.")

    # ── Fallback: faster-whisper ───────────────────────────────────────────────
    try:
        transcript = _transcribe_faster_whisper(wav_bytes)
        logger.info("faster-whisper transcription successful: %r", transcript[:80])
        return transcript
    except Exception as e:
        raise RuntimeError(f"Both transcription engines failed. Last error: {e}") from e
