import os
import json
import hashlib
from typing import Optional
from fastapi import HTTPException
import asyncio
from loguru import logger

# ---------------------------------------------------------------------------
# Configuration via environment variables
# ---------------------------------------------------------------------------
ASR_MODEL_SIZE = os.getenv("ASR_MODEL_SIZE", "small")
ASR_CACHE_DIR = os.getenv("ASR_CACHE_DIR", "data/asr_cache")
os.makedirs(ASR_CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Lazy model loading – avoid blocking import and allow graceful fallback
# ---------------------------------------------------------------------------
_model = None


def _get_model():
    """Lazily load the faster-whisper model on first use."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper model '{ASR_MODEL_SIZE}' …")
        _model = WhisperModel(ASR_MODEL_SIZE, device="cpu", compute_type="int8")
        logger.success("faster-whisper model loaded.")
    return _model


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(sha256: str) -> str:
    """Return the absolute path for a cache file associated with the given SHA256 hash."""
    return os.path.join(ASR_CACHE_DIR, f"{sha256}.json")


def load_cached(sha256: str) -> Optional[dict]:
    """Load a cached transcription if it exists.

    Returns the dictionary stored in the cache file or ``None`` if no cache entry.
    """
    cache_file = _cache_path(sha256)
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.error(f"Failed to read ASR cache {cache_file}: {exc}")
    return None


def save_cached(sha256: str, data: dict) -> None:
    """Persist a transcription result to the cache directory.

    The ``data`` dict should already contain the keys we want to store.
    """
    cache_file = _cache_path(sha256)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved ASR result to cache {cache_file}")
    except Exception as exc:
        logger.error(f"Failed to write ASR cache {cache_file}: {exc}")


# ---------------------------------------------------------------------------
# Core transcription
# ---------------------------------------------------------------------------

async def _run_faster_whisper(audio_path: str) -> dict:
    """Execute the synchronous faster-whisper transcription in a thread.

    Returns a dict with ``text``, ``language`` and ``confidence`` (if available).
    """
    def _sync_transcribe() -> dict:
        model = _get_model()
        # faster-whisper returns a 2-tuple: (segments_generator, info_namedtuple)
        segments_gen, info = model.transcribe(
            audio_path, word_timestamps=False, vad_filter=True,
            initial_prompt="Indian disaster report in Hindi or Hinglish. Place names, landmarks, flood, baadh, paani, rescue, ambulance, road blockage, fire, mandir, school, pulia, basti, mohalla."
        )
        # Consume the generator to collect all segment texts
        text = " ".join([seg.text for seg in segments_gen])
        language = getattr(info, "language", "")
        return {"text": text.strip(), "language": language, "confidence": None}

    return await asyncio.to_thread(_sync_transcribe)


async def transcribe(audio_path: str, sha256: str) -> dict:
    """Transcribe an audio file.

    1️⃣ Try to load a cached result.
    2️⃣ If no cache, run the faster-whisper model (treated as "LIVE").
    3️⃣ Save the live result to cache for future calls.

    Returns a dictionary compatible with the rest of the backend:
    ``{"text": ..., "language": ..., "mode": "LIVE"|"CACHED", "confidence": ...}``
    """
    # 1️⃣ Cached fallback
    cached = load_cached(sha256)
    if cached:
        logger.debug(f"ASR cache hit for {sha256}")
        return {
            "text": cached.get("text", ""),
            "language": cached.get("language", ""),
            "mode": "CACHED",
            "confidence": cached.get("confidence"),
        }

    # 2️⃣ Run the model (live transcription)
    try:
        live_result = await _run_faster_whisper(audio_path)
    except Exception as exc:
        logger.exception(f"faster-whisper transcription failed: {exc}")
        raise HTTPException(status_code=422, detail="Transcription failed")

    # 3️⃣ Store in cache
    cache_entry = {
        "text": live_result["text"],
        "language": live_result.get("language", ""),
        "confidence": live_result.get("confidence"),
    }
    save_cached(sha256, cache_entry)

    return {
        "text": live_result["text"],
        "language": live_result.get("language", ""),
        "mode": "LIVE",
        "confidence": live_result.get("confidence"),
    }
