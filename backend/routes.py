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
