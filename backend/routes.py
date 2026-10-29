from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from uuid import uuid4
import hashlib
import os
from .db import get_db, Base
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import List, Optional
import json

router = APIRouter()

# Simple Pydantic models for responses
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
    hazard_type: Optional[str]
    urgency: Optional[str]
    place_phrase_original: Optional[str]
    anchor_name: Optional[str]
    spatial_relation: Optional[str]
    geometry: Optional[dict]
    review_status: Optional[str]

# In‑memory store for demo (real DB usage would be via SQLAlchemy models)
# For brevity, we will just outline the logic; actual DB models are defined elsewhere.

@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/reports", response_model=ReportStatus)
async def upload_report(file: UploadFile = File(...), source_channel: str = "demo", db: AsyncSession = Depends(get_db)):
    # Simple validation
    if file.content_type not in ["audio/wav", "audio/mpeg", "audio/mp3"]:
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
    # Insert report placeholder into DB (pseudo‑code)
    # TODO: actual ORM insert
    # Start pipeline (in‑process for demo)
    from .services.pipeline import process_report
    await process_report(report_id, audio_path, sha256, db)
    return ReportStatus(id=report_id, status="processing")

@router.get("/api/reports/{report_id}", response_model=ReportStatus)
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    # Placeholder: fetch from DB and return current fields
    # For demo we return a static structure
    # TODO: replace with real query
    return ReportStatus(id=report_id, status="completed", transcript_mode="CACHED", transcript_text="example cached transcript", extraction={"hazard_type": "flood"}, location={"type": "Point", "coordinates": [77.0, 28.5]})

@router.get("/api/map-data")
async def map_data(db: AsyncSession = Depends(get_db)):
    # Build GeoJSON with incidents (points) and silent zones (polygons)
    # Fetch incidents
    incident_stmt = """
    SELECT id, location, approximate_area FROM incidents WHERE location IS NOT NULL
    """
    incident_res = await db.execute(incident_stmt)
    incident_rows = incident_res.fetchall()
    # Fetch silent zones with a demo threshold
    sz_stmt = """
    SELECT area_id, final_score FROM silent_zone_scores WHERE final_score >= 0.5
    """
    sz_res = await db.execute(sz_stmt)
    sz_rows = sz_res.fetchall()
    features = []
    for row in incident_rows:
        geom = row.location  # stored as WKT string
        # Convert WKT to GeoJSON using shapely (import lazily)
        from shapely.wkt import loads as load_wkt
        point = load_wkt(geom)
        features.append({
            "type": "Feature",
            "geometry": json.loads(json.dumps(point.__geo_interface__)),
            "properties": {"id": str(row.id), "type": "incident"},
        })
    for row in sz_rows:
        # Assume area_id corresponds to a tile geometry stored elsewhere; for demo we create a square tile
        x_str, y_str = row.area_id.split("_")
        x = float(x_str)
        y = float(y_str)
        size = float(os.getenv("TILE_SIZE_DEG", "0.01"))
        from shapely.geometry import Polygon
        poly = Polygon([
            (x, y),
            (x + size, y),
            (x + size, y + size),
            (x, y + size),
            (x, y),
        ])
        features.append({
            "type": "Feature",
            "geometry": json.loads(json.dumps(poly.__geo_interface__)),
            "properties": {"id": row.area_id, "type": "silent_zone", "score": row.final_score},
        })
    return {"type": "FeatureCollection", "features": features}

@router.patch("/api/incidents/{incident_id}")
async def patch_incident(incident_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    # Update incident with operator correction
    # Placeholder implementation
    return JSONResponse(content={"status": "updated"})

@router.post("/api/silent-zones/run")
async def run_silent_zones(db: AsyncSession = Depends(get_db)):
    from .services.silent_zones import compute_silent_zones
    await compute_silent_zones(db)
    return JSONResponse(content={"status": "silent zones computed"})
