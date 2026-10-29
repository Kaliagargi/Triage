import os
from shapely.geometry import Point, Polygon
from shapely.wkt import loads as load_wkt, dumps as dump_wkt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Gazetteer

# Configuration for buffer sizes (meters)
NEAR_BUFFER_M = float(os.getenv("NEAR_BUFFER_M", "100"))
BEHIND_BUFFER_M = float(os.getenv("BEHIND_BUFFER_M", "200"))

def _buffer_point(point: Point, meters: float) -> Polygon:
    # Approximate buffering in latitude/longitude (degrees) – for demo use a simple factor
    # 1 degree ≈ 111,000 meters at equator; we use a rough conversion
    deg = meters / 111000.0
    return point.buffer(deg)

async def resolve_location(place_phrase: str, anchor_name: str, relation: str, db: AsyncSession) -> dict:
    """Resolve a landmark‑relative phrase.
    Returns a dict with WKT strings and confidence.
    """
    # Normalise inputs
    anchor_name_norm = anchor_name.strip().lower()
    # Find anchor in gazetteer (match name or alias)
    stmt = select(Gazetteer).where(
        (Gazetteer.normalized_name == anchor_name_norm) |
        (Gazetteer.aliases.any(anchor_name_norm))
    )
    result = await db.execute(stmt)
    gaz = result.scalar_one_or_none()
    if not gaz:
        return {
            "point_wkt": None,
            "buffer_wkt": None,
            "confidence": 0.0,
            "needs_review": True,
        }
    point = load_wkt(gaz.location)  # assumes POINT
    # Choose buffer based on relation
    rel = relation.lower()
    if rel in {"near", "paas", "near to", "close to"}:
        buffer = _buffer_point(point, NEAR_BUFFER_M)
    elif rel in {"behind", "peeche", "back", "behind of"}:
        buffer = _buffer_point(point, BEHIND_BUFFER_M)
    elif rel in {"in front of", "samne", "front"}:
        buffer = _buffer_point(point, NEAR_BUFFER_M)
    else:
        # Unsupported relation – return point only but flag review
        return {
            "point_wkt": dump_wkt(point),
            "buffer_wkt": None,
            "confidence": 0.5,
            "needs_review": True,
        }
    return {
        "point_wkt": dump_wkt(point),
        "buffer_wkt": dump_wkt(buffer),
        "confidence": 0.9,
        "needs_review": False,
    }
