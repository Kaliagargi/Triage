import os
import hashlib
import json
from typing import Optional
from fastapi import HTTPException
import aiohttp
import asyncio

# Simple configuration via env vars
ASR_LIVE_ENDPOINT = os.getenv("ASR_LIVE_ENDPOINT", "")  # e.g., Bhashini API URL
ASR_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "15"))  # seconds

# Cached transcripts mapping SHA256 -> transcript dict
CACHE_DIR = os.getenv("ASR_CACHE_DIR", "data/asr_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

async def live_asr(audio_path: str) -> Optional[dict]:
    if not ASR_LIVE_ENDPOINT:
        return None
    async with aiohttp.ClientSession() as session:
        try:
            with open(audio_path, "rb") as f:
                data = f.read()
            async with session.post(
                ASR_LIVE_ENDPOINT,
                data=data,
                timeout=ASR_TIMEOUT,
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                return {
                    "text": result.get("transcript", ""),
                    "language": result.get("language", ""),
                    "confidence": result.get("confidence"),
                }
        except Exception:
            return None

def load_cached(sha256: str) -> Optional[dict]:
    cache_file = os.path.join(CACHE_DIR, f"{sha256}.json")
    if os.path.isfile(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

async def transcribe(audio_path: str, sha256: str) -> dict:
    """Return dict with keys: text, language, mode (LIVE/CACHED), confidence, error (optional)."""
    # Attempt live ASR first
    live_result = await live_asr(audio_path)
    if live_result:
        return {
            "text": live_result["text"],
            "language": live_result["language"],
            "mode": "LIVE",
            "confidence": live_result.get("confidence"),
        }
    # Fallback to cached if exists
    cached = load_cached(sha256)
    if cached:
        return {
            "text": cached["text"],
            "language": cached.get("language", ""),
            "mode": "CACHED",
            "confidence": cached.get("confidence"),
        }
    # No result
    raise HTTPException(status_code=422, detail="Transcription failed and no cached fixture available")
