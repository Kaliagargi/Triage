"""Local Telegram polling adapter for TRACE-India demo reports.

Run from the repository root:
    python -m backend.telegram_bot

Keep TELEGRAM_BOT_TOKEN in your local environment/.env; never commit it.
"""
import asyncio
import hashlib
import json
import os
import re
import time
from uuid import uuid4

import requests
from loguru import logger
from shapely.geometry import Point, mapping
from sqlalchemy import select

from .db import AsyncSessionLocal
from .models import Report, Incident, Gazetteer
from .services.extraction import extract_fields
from .services.landmark_geocoding import resolve_location

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
VADODARA = (22.3072, 73.1812)
POLL_TIMEOUT = 25


def api_call(method: str, payload=None, files=None):
    response = requests.post(f"{API}/{method}", data=payload or {}, files=files, timeout=40)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description", f"Telegram API error: {method}"))
    return body["result"]


def send_message(chat_id: int, text: str):
    api_call("sendMessage", {"chat_id": chat_id, "text": text})


async def find_shared_location(db, text: str):
    """Resolve only exact gazetteer names/aliases mentioned in text."""
    normalized = re.sub(r"[^\w\s]", " ", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    rows = (await db.execute(select(Gazetteer))).scalars().all()
    for row in rows:
        candidates = [row.name] + (row.aliases or [])
        if isinstance(row.aliases, str):
            try:
                candidates += json.loads(row.aliases)
            except (ValueError, TypeError):
                pass
        for name in candidates:
            name_norm = re.sub(r"[^\w\s]", " ", str(name).lower())
            name_norm = re.sub(r"\s+", " ", name_norm).strip()
            if name_norm and re.search(rf"\b{re.escape(name_norm)}\b", normalized):
                try:
                    geo = row.location
                    if isinstance(geo, str):
                        geo = json.loads(geo)
                    point = Point(float(geo["coordinates"][0]), float(geo["coordinates"][1]))
                    return row.name, point.y, point.x
                except Exception:
                    continue
    return None, None, None


async def persist_text_report(message: dict, transcript: str, pin=None):
    chat_id = message["chat"]["id"]
    report_id, incident_id = str(uuid4()), str(uuid4())
    digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()

    async with AsyncSessionLocal() as db:
        fields = extract_fields(transcript, digest)
        anchor = fields.get("anchor_name")
        relation = fields.get("spatial_relation")
        resolved = await resolve_location(
            fields.get("place_phrase") or transcript, anchor or "", relation or "", db
        ) if anchor else None

        lat = lon = None
        geo = None
        area = None
        location_confidence = 0.0
        if pin:
            lat, lon = pin
            geo = mapping(Point(lon, lat))
            location_confidence = 1.0
        elif resolved and resolved.get("point_lat") is not None:
            lat, lon = resolved["point_lat"], resolved["point_lon"]
            geo = resolved["point_geojson"]
            area = resolved.get("buffer_geojson")
            location_confidence = resolved.get("confidence", 0.0)

        report = Report(
            id=report_id,
            audio_path="",
            audio_sha256=digest,
            source_channel="telegram",
            status="completed",
            transcript_text=transcript,
            transcript_mode="TEXT",
            transcript_language="unknown",
            extraction=fields,
            is_synthetic=False,
        )
        db.add(report)
        await db.flush()

        incident = Incident(
            id=incident_id,
            report_id=report_id,
            hazard_type=fields.get("hazard_type"),
            urgency=fields.get("urgency") or "unknown",
            water_level_description=fields.get("water_level_description"),
            needs=fields.get("needs") or [],
            place_phrase_original=fields.get("place_phrase"),
            anchor_name=anchor,
            spatial_relation=relation,
            location=geo,
            approximate_area=area,
            lat=lat,
            lon=lon,
            extraction_confidence=fields.get("confidence", 0.0),
            location_confidence=location_confidence,
            review_status="unverified",
            is_synthetic=False,
        )
        db.add(incident)
        await db.commit()
    return incident_id, fields, lat, lon


def handle_audio(message: dict, chat_id: int):
    # Audio is routed through the existing report/transcription pipeline.
    # Download the Telegram file first, then create a Report and call process_report.
    send_message(chat_id, "I received your voice note. Audio transcription integration is being connected; for this initial test, please also send the location or a short text summary.")


async def process_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = message["chat"]["id"]

    if message.get("location"):
        pin = (message["location"]["latitude"], message["location"]["longitude"])
        send_message(chat_id, "Location pin received. Now send a short description of the incident in the same chat.")
        # Save pin in memory only for this polling process; next text uses it.
        PENDING_PINS[chat_id] = pin
        return

    if message.get("voice") or message.get("audio"):
        handle_audio(message, chat_id)
        return

    transcript = (message.get("text") or message.get("caption") or "").strip()
    if not transcript or transcript.startswith("/"):
        if transcript == "/start":
            send_message(chat_id, "TRACE-India Vadodara demo bot. Send a flood/disaster report as text, or share a Telegram location pin followed by a text description. Reports appear on the operator dashboard as UNVERIFIED.")
        return
        return

    pin = PENDING_PINS.pop(chat_id, None)
    incident_id, fields, lat, lon = await persist_text_report(message, transcript, pin)
    location_text = f"\nLocation: {lat:.5f}, {lon:.5f}" if lat is not None else "\nLocation: not resolved — please share a Telegram location pin."
    send_message(
        chat_id,
        "Report received by TRACE-India.\n"
        f"Incident ID: {incident_id[:8]}\n"
        f"Hazard detected: {fields.get('hazard_type') or 'uncertain'}\n"
        f"Urgency: {fields.get('urgency') or 'unknown'}"
        + location_text
        + "\nStatus: UNVERIFIED. Please call local emergency services for immediate danger."
    )


PENDING_PINS = {}


def main():
    if not TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN. Set it in your local environment before starting.")
    offset = None
    logger.info("TRACE Telegram bot polling started")
    while True:
        try:
            updates = api_call("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset})
            for update in updates:
                offset = update["update_id"] + 1
                asyncio.run(process_update(update))
        except KeyboardInterrupt:
            logger.info("Telegram bot stopped")
            break
        except Exception:
            logger.exception("Telegram polling iteration failed")
            time.sleep(3)


if __name__ == "__main__":
    main()
