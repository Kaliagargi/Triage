from fastapi import BackgroundTasks
from .asr import transcribe
from .extraction import extract_fields
from .landmark_geocoding import resolve_location
from ..db import get_db
from ..models import Report, Incident
import uuid
import os
import json

async def process_report(report_id: str, audio_path: str, sha256: str, db) -> None:
    """Orchestrates the pipeline for a single report.
    Steps:
    1. Transcription (live with fallback to cached).
    2. Deterministic extraction of incident fields.
    3. Landmark‑relative geocoding.
    4. Store Incident record.
    """
    # 1. Transcription
    try:
        transcription = await transcribe(audio_path, sha256)
    except Exception as e:
        # Update report status with error
        await db.execute(
            "UPDATE reports SET status=:s, transcription_error=:e WHERE id=:i",
            {"s": "failed", "e": str(e), "i": report_id},
        )
        await db.commit()
        return

    # Store transcription in Report
    await db.execute(
        "UPDATE reports SET status='transcribed', transcript_text=:t, transcript_language=:l, transcript_mode=:m WHERE id=:i",
        {"t": transcription["text"], "l": transcription["language"], "m": transcription["mode"], "i": report_id},
    )
    await db.commit()

    # 2. Extraction (deterministic fixture mapping based on sha256)
    extraction = extract_fields(transcription["text"], sha256)
    # Update report with extraction JSON (stored as text for simplicity)
    await db.execute(
        "UPDATE reports SET status='extracted', extraction=:e WHERE id=:i",
        {"e": json.dumps(extraction), "i": report_id},
    )
    await db.commit()

    # 3. Landmark geocoding
    location_res = resolve_location(extraction.get("place_phrase"), extraction.get("anchor_name"), extraction.get("spatial_relation"))

    # 4. Create Incident row
    incident_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO incidents (id, report_id, hazard_type, urgency, water_level_description, needs,
                               place_phrase_original, anchor_name, spatial_relation, location, approximate_area,
                               location_confidence, extraction_confidence, review_status)
        VALUES (:id, :rid, :haz, :urg, :wl, :needs, :place, :anchor, :rel, :loc, :area, :loc_conf, :ext_conf, :rev)
        """,
        {
            "id": incident_id,
            "rid": report_id,
            "haz": extraction.get("hazard_type"),
            "urg": extraction.get("urgency"),
            "wl": extraction.get("water_level_description"),
            "needs": extraction.get("needs"),
            "place": extraction.get("place_phrase"),
            "anchor": extraction.get("anchor_name"),
            "rel": extraction.get("spatial_relation"),
            "loc": location_res.get("point_wkt"),
            "area": location_res.get("buffer_wkt"),
            "loc_conf": location_res.get("confidence"),
            "ext_conf": extraction.get("confidence"),
            "rev": "needs_review" if location_res.get("needs_review") else "completed",
        },
    )
    await db.commit()

    # Update report status to completed
    await db.execute("UPDATE reports SET status='completed' WHERE id=:i", {"i": report_id})
    await db.commit()
