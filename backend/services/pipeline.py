import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..models import Report, Incident
from .asr import transcribe
from .extraction import extract_fields
from .landmark_geocoding import resolve_location


async def _update_report_status(
    db: AsyncSession, report_id: str, **fields: Any
) -> None:
    """Utility to update a Report row with arbitrary keyword fields.

    ``fields`` are column names mapped to values; they are merged into an ``update``
    statement executed via the async session.
    """
    await db.execute(
        Report.__table__.update()
        .where(Report.id == report_id)
        .values(**fields)
    )
    await db.commit()


async def process_report(
    report_id: str, audio_path: str, sha256: str, db: AsyncSession
) -> None:
    """Orchestrate the full pipeline for a submitted audio report.

    Steps:
    1️⃣ Transcribe (live via faster-whisper, fallback to cache).
    2️⃣ Deterministic extraction (fixture-based).
    3️⃣ Landmark geocoding to obtain a GeoJSON point and buffer polygon.
    4️⃣ Persist an ``Incident`` record linked to the ``Report``.
    5️⃣ Mark the report as ``completed``.
    """
    # -----------------------------------------------------------------
    # 1️⃣ Transcription
    # -----------------------------------------------------------------
    try:
        transcription = await transcribe(audio_path, sha256)
    except HTTPException as exc:
        await _update_report_status(
            db, report_id, status="failed", transcription_error=exc.detail
        )
        raise
    except Exception as exc:
        await _update_report_status(
            db, report_id, status="failed", transcription_error=str(exc)
        )
        raise HTTPException(status_code=500, detail="ASR processing failed")

    # Store transcription results on the report
    await _update_report_status(
        db,
        report_id,
        status="transcribed",
        transcript_text=transcription.get("text"),
        transcript_language=transcription.get("language"),
        transcript_mode=transcription.get("mode"),
    )
    logger.info(f"Report {report_id}: transcription complete ({transcription.get('mode')})")

    # -----------------------------------------------------------------
    # 2️⃣ Extraction (fixture based)
    # -----------------------------------------------------------------
    extraction = extract_fields(transcription.get("text", ""), sha256)

    # Store extraction as JSON string — the column is JSON type but we
    # pass the dict directly so SQLAlchemy serialises it properly.
    await _update_report_status(
        db,
        report_id,
        status="extracted",
        extraction=extraction,
    )
    logger.info(f"Report {report_id}: extraction complete (confidence={extraction.get('confidence')})")

    # -----------------------------------------------------------------
    # 3️⃣ Landmark geocoding (point + buffer)
    # -----------------------------------------------------------------
    location_res = await resolve_location(
        extraction.get("place_phrase", ""),
        extraction.get("anchor_name", ""),
        extraction.get("spatial_relation", ""),
        db,
    )
    logger.info(
        f"Report {report_id}: geocoding complete "
        f"(confidence={location_res.get('confidence')}, "
        f"needs_review={location_res.get('needs_review')})"
    )

    # -----------------------------------------------------------------
    # 4️⃣ Create Incident ORM object
    # -----------------------------------------------------------------
    incident = Incident(
        id=str(uuid.uuid4()),
        report_id=report_id,
        hazard_type=extraction.get("hazard_type"),
        urgency=extraction.get("urgency"),
        water_level_description=extraction.get("water_level_description"),
        needs=extraction.get("needs"),
        place_phrase_original=extraction.get("place_phrase"),
        anchor_name=extraction.get("anchor_name"),
        spatial_relation=extraction.get("spatial_relation"),
        location=location_res.get("point_geojson"),
        approximate_area=location_res.get("buffer_geojson"),
        location_confidence=location_res.get("confidence"),
        extraction_confidence=extraction.get("confidence"),
        review_status="needs_review" if location_res.get("needs_review") else "completed",
        lat=location_res.get("point_lat"),
        lon=location_res.get("point_lon"),
    )
    db.add(incident)
    await db.commit()
    logger.info(f"Report {report_id}: incident {incident.id} created")

    # -----------------------------------------------------------------
    # 5️⃣ Final report status
    # -----------------------------------------------------------------
    await _update_report_status(db, report_id, status="completed")
    logger.success(f"Report {report_id}: pipeline complete ✓")
