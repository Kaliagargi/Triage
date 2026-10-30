from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from uuid import uuid4
import hashlib
import json
import os
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Point, mapping
from loguru import logger

from .db import get_db
from .models import Report, Incident, Feedback, SilentZoneScore

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class ReportStatus(BaseModel):
    id: str
    status: str
    transcript_mode: Optional[str] = None
    transcript_text: Optional[str] = None
    extraction: Optional[dict] = None
    location: Optional[dict] = None
    transcript_language: Optional[str] = None
    transcription_error: Optional[str] = None
    fixture_id: Optional[str] = None
    incident: Optional[dict] = None


class IncidentResponse(BaseModel):
    id: str
    report_id: str
    hazard_type: Optional[str] = None
    urgency: Optional[str] = None
    place_phrase_original: Optional[str] = None
    anchor_name: Optional[str] = None
    spatial_relation: Optional[str] = None
    geometry: Optional[dict] = None
    review_status: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    needs: Optional[List[str]] = None
    extraction_confidence: Optional[float] = None
    location_confidence: Optional[float] = None
    water_level_description: Optional[str] = None


class IncidentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hazard_type: Optional[str] = Field(default=None, max_length=80)
    urgency: Optional[str] = Field(default=None, max_length=40)
    water_level_description: Optional[str] = Field(default=None, max_length=200)
    needs: Optional[List[str]] = None
    place_phrase_original: Optional[str] = Field(default=None, max_length=300)
    anchor_name: Optional[str] = Field(default=None, max_length=160)
    spatial_relation: Optional[str] = Field(default=None, max_length=80)
    review_status: Optional[str] = Field(default=None, max_length=40)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def require_non_empty_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@router.post("/api/reports", response_model=ReportStatus)
async def upload_report(
    file: UploadFile = File(...),
    source_channel: str = "demo",
    db: AsyncSession = Depends(get_db),
):
    # Validate content type
    allowed = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/x-wav", "audio/wave", "audio/webm", "video/webm", "audio/ogg", "application/ogg", "audio/mp4", "audio/m4a", "audio/aac"}
    suffix = Path(file.filename or "").suffix.lower()
    allowed_suffixes = {".wav", ".mp3", ".webm", ".ogg", ".m4a", ".aac"}
    if file.content_type not in allowed and suffix not in allowed_suffixes:
        raise HTTPException(status_code=400, detail="Unsupported audio format. Use WAV, MP3, WEBM, OGG, M4A or AAC.")
    if not suffix:
        suffix = { "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/mp4": ".m4a", "audio/m4a": ".m4a", "audio/aac": ".aac" }.get(file.content_type, ".audio")

    contents = await file.read()
    sha256 = hashlib.sha256(contents).hexdigest()
    report_id = str(uuid4())

    # Save audio to storage directory
    storage_dir = os.getenv("AUDIO_STORAGE", "storage/audio")
    os.makedirs(storage_dir, exist_ok=True)
    audio_path = os.path.join(storage_dir, f"{report_id}{suffix}")
    with open(audio_path, "wb") as f:
        f.write(contents)

    # ---- INSERT the Report row FIRST ----
    report = Report(
        id=report_id,
        audio_path=audio_path,
        audio_sha256=sha256,
        source_channel=source_channel,
        status="pending",
    )
    db.add(report)
    await db.commit()
    logger.info(f"Report {report_id} created (sha256={sha256[:12]}…)")

    # Run pipeline (in-process for demo)
    from .services.pipeline import process_report
    await process_report(report_id, audio_path, sha256, db)

    saved_report = (await db.execute(select(Report).where(Report.id == report_id))).scalar_one()
    saved_incident = (await db.execute(select(Incident).where(Incident.report_id == report_id))).scalar_one_or_none()
    return ReportStatus(
        id=saved_report.id, status=saved_report.status,
        transcript_mode=saved_report.transcript_mode,
        transcript_text=saved_report.transcript_text,
        transcript_language=saved_report.transcript_language,
        transcription_error=saved_report.transcription_error,
        extraction=saved_report.extraction, fixture_id=saved_report.fixture_id,
        location=saved_incident.location if saved_incident else None,
        incident=({
            "id": saved_incident.id, "hazard_type": saved_incident.hazard_type,
            "urgency": saved_incident.urgency, "needs": saved_incident.needs,
            "anchor_name": saved_incident.anchor_name,
            "spatial_relation": saved_incident.spatial_relation,
            "lat": saved_incident.lat, "lon": saved_incident.lon,
            "review_status": saved_incident.review_status,
        } if saved_incident else None),
    )


@router.get("/api/reports/{report_id}", response_model=ReportStatus)
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Report).where(Report.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Parse extraction JSON if stored as string
    extraction = report.extraction
    if isinstance(extraction, str):
        try:
            extraction = json.loads(extraction)
        except (json.JSONDecodeError, TypeError):
            extraction = None

    return ReportStatus(
        id=report.id,
        status=report.status,
        transcript_mode=report.transcript_mode,
        transcript_text=report.transcript_text,
        extraction=extraction,
        location=(await db.execute(select(Incident.location).where(Incident.report_id == report.id))).scalar_one_or_none(),
        transcript_language=report.transcript_language,
        transcription_error=report.transcription_error,
        fixture_id=report.fixture_id,
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

@router.get("/api/incidents", response_model=List[IncidentResponse])
async def list_incidents(
    review_status: Optional[str] = None,
    hazard_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    stmt = select(Incident)
    if review_status:
        stmt = stmt.where(Incident.review_status == review_status)
    if hazard_type:
        stmt = stmt.where(Incident.hazard_type == hazard_type)
    stmt = stmt.order_by(Incident.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    incidents = result.scalars().all()
    return [
        IncidentResponse(
            id=inc.id,
            report_id=inc.report_id,
            hazard_type=inc.hazard_type,
            urgency=inc.urgency,
            place_phrase_original=inc.place_phrase_original,
            anchor_name=inc.anchor_name,
            spatial_relation=inc.spatial_relation,
            geometry=inc.location,
            review_status=inc.review_status,
            lat=inc.lat,
            lon=inc.lon,
            needs=inc.needs,
            extraction_confidence=inc.extraction_confidence,
            location_confidence=inc.location_confidence,
            water_level_description=inc.water_level_description,
        )
        for inc in incidents
    ]


@router.patch("/api/incidents/{incident_id}")
async def patch_incident(
    incident_id: str,
    payload: IncidentPatch,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    changes = payload.model_dump(exclude_unset=True)
    new_lat = changes.get("lat", incident.lat)
    new_lon = changes.get("lon", incident.lon)
    if (new_lat is None) != (new_lon is None):
        raise HTTPException(status_code=422, detail="Latitude and longitude must both be available")

    for field, new_value in changes.items():
        old_value = getattr(incident, field)
        if old_value == new_value:
            continue
        setattr(incident, field, new_value)
        db.add(Feedback(
            id=str(uuid4()),
            incident_id=incident_id,
            field=field,
            before=json.dumps(old_value, ensure_ascii=False, default=str),
            after=json.dumps(new_value, ensure_ascii=False, default=str),
        ))

    if "lat" in changes or "lon" in changes:
        incident.location = mapping(Point(float(new_lon), float(new_lat))) if new_lat is not None else None
        incident.review_status = "confirmed"

    await db.commit()
    logger.info(f"Incident {incident_id} patched: {list(changes.keys())}")
    return {"status": "updated", "fields": list(changes.keys()), "incident_id": incident_id}


@router.get("/api/map-data")
async def map_data(db: AsyncSession = Depends(get_db)):
    """Return incident points and approximate-location polygons as GeoJSON."""
    result = await db.execute(select(Incident).order_by(Incident.created_at.desc()))
    incidents = result.scalars().all()
    features = []

    for inc in incidents:
        common = {
            "id": inc.id,
            "hazard_type": inc.hazard_type,
            "urgency": inc.urgency,
            "review_status": inc.review_status,
            "extraction_confidence": inc.extraction_confidence,
            "location_confidence": inc.location_confidence,
            "is_synthetic": bool(inc.is_synthetic),
        }
        if inc.lat is not None and inc.lon is not None:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [inc.lon, inc.lat]},
                "properties": {**common, "type": "incident"},
            })
        if inc.approximate_area:
            area = inc.approximate_area
            if isinstance(area, str):
                try:
                    area = json.loads(area)
                except (json.JSONDecodeError, TypeError):
                    area = None
            if isinstance(area, dict) and area.get("type") and area.get("coordinates"):
                features.append({
                    "type": "Feature",
                    "geometry": area,
                    "properties": {**common, "type": "uncertainty_area"},
                })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Silent zones
# ---------------------------------------------------------------------------

@router.post("/api/silent-zones/run")
async def run_silent_zones(db: AsyncSession = Depends(get_db)):
    from .services.silent_zones import compute_silent_zones
    try:
        return await compute_silent_zones(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/silent-zones/latest")
async def get_latest_silent_zones(db: AsyncSession = Depends(get_db)):
    """Return the latest silent-zone snapshot as a GeoJSON FeatureCollection."""
    from .services.silent_zones import polygon_for_area_id

    result = await db.execute(
        select(SilentZoneScore).order_by(SilentZoneScore.run_at.desc(), SilentZoneScore.id.desc())
    )
    rows = result.scalars().all()
    latest_by_area = {}
    for row in rows:
        latest_by_area.setdefault(row.area_id, row)

    features = []
    for area_id, row in latest_by_area.items():
        try:
            geometry = mapping(polygon_for_area_id(area_id))
        except (TypeError, ValueError):
            logger.warning("Skipping malformed silent-zone area id: %s", area_id)
            continue
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "area_id": area_id,
                "observed_reports": row.observed_reports,
                "expected_reports": row.expected_reports,
                "silence_factor": row.silence_factor,
                "silent_score": row.final_score,
                "quality_flag": row.quality_flag,
                "config_version": row.config_version,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "quality_flag": "ILLUSTRATIVE_BASELINE" if features else None,
            "note": "Scores use configurable demo baselines; they are not measured population or hazard risk.",
        },
    }
