import json
import os
from shapely.geometry import Point, Polygon, shape, mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..models import Gazetteer

# Configuration for buffer sizes (meters)
NEAR_BUFFER_M = float(os.getenv("NEAR_BUFFER_M", "100"))
BEHIND_BUFFER_M = float(os.getenv("BEHIND_BUFFER_M", "200"))


def _buffer_point(point: Point, meters: float) -> Polygon:
    """Approximate buffering in latitude/longitude (degrees).

    1 degree ≈ 111,000 meters at equator; we use a rough conversion for demo.
    """
    deg = meters / 111000.0
    return point.buffer(deg)


async def resolve_location(
    place_phrase: str, anchor_name: str, relation: str, db: AsyncSession
) -> dict:
    """Resolve a landmark-relative phrase to a GeoJSON point + buffer.

    Returns a dict with GeoJSON geometries, lat/lon, confidence, and review flag.
    Keys returned (aligned with pipeline.py expectations):
        - point_geojson: GeoJSON dict or None
        - buffer_geojson: GeoJSON dict or None
        - point_lat: float or None
        - point_lon: float or None
        - confidence: float
        - needs_review: bool
    """
    anchor_name_norm = (anchor_name or "").strip().lower()
    if not anchor_name_norm:
        return _empty_result()

    # ------------------------------------------------------------------
    # Find anchor in gazetteer by normalized_name first
    # ------------------------------------------------------------------
    stmt = select(Gazetteer).where(Gazetteer.normalized_name == anchor_name_norm)
    result = await db.execute(stmt)
    gaz = result.scalar_one_or_none()

    # Fallback: scan aliases in Python (JSON column — no SQL array operator on SQLite)
    if gaz is None:
        all_stmt = select(Gazetteer)
        all_result = await db.execute(all_stmt)
        for row in all_result.scalars().all():
            aliases = row.aliases or []
            # aliases is stored as a JSON list of strings
            if isinstance(aliases, str):
                try:
                    aliases = json.loads(aliases)
                except (json.JSONDecodeError, TypeError):
                    aliases = []
            if anchor_name_norm in [a.lower().strip() for a in aliases]:
                gaz = row
                break

    if gaz is None:
        logger.debug(f"No gazetteer match for anchor '{anchor_name_norm}'")
        return _empty_result()

    # ------------------------------------------------------------------
    # Parse the stored GeoJSON location into a Shapely point
    # ------------------------------------------------------------------
    location_data = gaz.location
    if isinstance(location_data, str):
        try:
            location_data = json.loads(location_data)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Could not parse gazetteer location for '{gaz.name}'")
            return _empty_result()

    try:
        point = shape(location_data)
    except Exception as exc:
        logger.warning(f"Invalid geometry for gazetteer '{gaz.name}': {exc}")
        return _empty_result()

    # ------------------------------------------------------------------
    # Choose buffer based on spatial relation
    # ------------------------------------------------------------------
    rel = (relation or "").lower()
    if rel in {"near", "paas", "near to", "close to"}:
        buffer_poly = _buffer_point(point, NEAR_BUFFER_M)
    elif rel in {"behind", "peeche", "back", "behind of"}:
        buffer_poly = _buffer_point(point, BEHIND_BUFFER_M)
    elif rel in {"in front of", "samne", "front", "opposite", "vipreet"}:
        buffer_poly = _buffer_point(point, NEAR_BUFFER_M)
    else:
        # Unsupported relation – return point only but flag review
        return {
            "point_geojson": mapping(point),
            "buffer_geojson": None,
            "point_lat": point.y,
            "point_lon": point.x,
            "confidence": 0.5,
            "needs_review": True,
        }

    return {
        "point_geojson": mapping(point),
        "buffer_geojson": mapping(buffer_poly),
        "point_lat": point.y,
        "point_lon": point.x,
        "confidence": 0.9,
        "needs_review": False,
    }


def _empty_result() -> dict:
    """Return a result dict when no geocoding match is found."""
    return {
        "point_geojson": None,
        "buffer_geojson": None,
        "point_lat": None,
        "point_lon": None,
        "confidence": 0.0,
        "needs_review": True,
    }
