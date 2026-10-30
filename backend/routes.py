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
from pydantic import BaseModel
from loguru import logger

from .db import get_db
from .models import Report, Incident, Feedback

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
    allowed = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/x-wav", "audio/wave"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    contents = await file.read()
    sha256 = hashlib.sha256(contents).hexdigest()
    report_id = str(uuid4())

    # Save audio to storage directory
    storage_dir = os.getenv("AUDIO_STORAGE", "storage/audio")
    os.makedirs(storage_dir, exist_ok=True)
    audio_path = os.path.join(storage_dir, f"{report_id}.wav")
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

    return ReportStatus(id=report_id, status="processing")


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
        location=None,
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

@router.get("/api/incidents", response_model=List[IncidentResponse])
async def list_incidents(db: AsyncSession = Depends(get_db)):
    stmt = select(Incident).order_by(Incident.created_at.desc())
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
        )
        for inc in incidents
    ]


@router.patch("/api/incidents/{incident_id}")
async def patch_incident(
    incident_id: str, payload: dict, db: AsyncSession = Depends(get_db)
):
    # Fetch the incident
    stmt = select(Incident).where(Incident.id == incident_id)
    result = await db.execute(stmt)
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Updatable fields
    updatable = {
        "hazard_type", "urgency", "water_level_description", "needs",
        "place_phrase_original", "anchor_name", "spatial_relation",
        "review_status", "lat", "lon",
    }

    for field, new_value in payload.items():
        if field not in updatable:
            continue
        old_value = getattr(incident, field, None)
        setattr(incident, field, new_value)
        # Record feedback for audit trail
        fb = Feedback(
            id=str(uuid4()),
            incident_id=incident_id,
            field=field,
            before=str(old_value) if old_value is not None else None,
            after=str(new_value) if new_value is not None else None,
        )
        db.add(fb)

    await db.commit()
    logger.info(f"Incident {incident_id} patched: {list(payload.keys())}")
    return JSONResponse(content={"status": "updated", "fields": list(payload.keys())})


# ---------------------------------------------------------------------------
# Map data (GeoJSON FeatureCollection from incidents)
# ---------------------------------------------------------------------------

@router.get("/api/map-data")
async def map_data(db: AsyncSession = Depends(get_db)):
    """Build a GeoJSON FeatureCollection from incidents with coordinates."""
    stmt = select(Incident).where(Incident.lat.isnot(None)).where(Incident.lon.isnot(None))
    result = await db.execute(stmt)
    incidents = result.scalars().all()

    features = []
    for inc in incidents:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [inc.lon, inc.lat],
            },
            "properties": {
                "id": inc.id,
                "type": "incident",
                "hazard_type": inc.hazard_type,
                "urgency": inc.urgency,
                "review_status": inc.review_status,
            },
        })
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Destinations (seeded India cities)
# ---------------------------------------------------------------------------

@router.get("/api/destinations")
async def get_destinations():
    """Serve static GeoJSON of seeded Indian destinations."""
    data_path = Path(__file__).parent / "data" / "india_destinations.geojson"
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="Destinations data not found")
    with open(data_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    return content


# ---------------------------------------------------------------------------
# Silent zones
# ---------------------------------------------------------------------------

@router.post("/api/silent-zones/run")
async def run_silent_zones(db: AsyncSession = Depends(get_db)):
    from .services.silent_zones import compute_silent_zones
    await compute_silent_zones(db)
    return JSONResponse(content={"status": "silent zones computed"})
