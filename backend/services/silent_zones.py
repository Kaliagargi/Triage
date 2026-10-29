import os
import math
from typing import List
from sqlalchemy import select, func, insert, text, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Report, SilentZoneScore, Incident, Gazetteer
from shapely.geometry import shape, Polygon, mapping
from shapely.wkt import loads as load_wkt, dumps as dump_wkt

# Configuration: buffer size for tiling (in degrees approx)
TILE_SIZE_DEG = float(os.getenv("TILE_SIZE_DEG", "0.01"))  # ~1km at equator

async def _aggregate_reports(db: AsyncSession) -> List[dict]:
    """Aggregate observed report counts per tile.
    Returns a list of dicts: {"tile_id": str, "observed": int, "geom": Polygon}
    """
    # Simple tiling: floor(lon/size)*size, floor(lat/size)*size
    # Use raw SQL for performance
    stmt = """
    SELECT
        floor(ST_X(geom)::numeric / :size) * :size AS tile_x,
        floor(ST_Y(geom)::numeric / :size) * :size AS tile_y,
        COUNT(*) AS observed,
        ST_Collect(geom) AS geom
    FROM reports
    WHERE status = 'completed'
    GROUP BY tile_x, tile_y;
    """
    result = await db.execute(stmt, {"size": TILE_SIZE_DEG})
    rows = result.fetchall()
    tiles = []
    for row in rows:
        tile_id = f"{row.tile_x}_{row.tile_y}"
        poly = Polygon([
            (row.tile_x, row.tile_y),
            (row.tile_x + TILE_SIZE_DEG, row.tile_y),
            (row.tile_x + TILE_SIZE_DEG, row.tile_y + TILE_SIZE_DEG),
            (row.tile_x, row.tile_y + TILE_SIZE_DEG),
            (row.tile_x, row.tile_y),
        ])
        tiles.append({"tile_id": tile_id, "observed": row.observed, "geom": poly})
    return tiles

async def compute_silent_zones(db: AsyncSession):
    """Compute silent‑zone scores for each tile and store them.
    Uses the corrected formula from section 6.3:
        silence_factor = 1 / (1 + observed / max(expected, epsilon))
        final_score = exposure * vulnerability * silence_factor * (1 + lambda_ * connectivity_penalty)
    For demo purposes, exposure, vulnerability, expected, connectivity_penalty are stubbed
    with deterministic values.
    """
    epsilon = 1e-6
    lambda_ = 0.5
    # Step 1: aggregate observed reports per tile
    tiles = await _aggregate_reports(db)
    for tile in tiles:
        observed = tile["observed"]
        # Stubbed expected reports – could be based on population density; use a constant for demo
        expected = 5.0
        exposure = 1.0  # assume uniform exposure
        vulnerability = 1.0
        connectivity_penalty = 0.0
        silence_factor = 1.0 / (1.0 + observed / max(expected, epsilon))
        final_score = (
            exposure * vulnerability * silence_factor * (1.0 + lambda_ * connectivity_penalty)
        )
        # Insert or upsert into SilentZoneScore table
        insert_stmt = insert(SilentZoneScore).values(
            id=str(uuid4()),
            area_id=tile["tile_id"],
            run_at=func.now(),
            exposure=exposure,
            vulnerability=vulnerability,
            observed_reports=observed,
            expected_reports=expected,
            connectivity_penalty=connectivity_penalty,
            silence_factor=silence_factor,
            final_score=final_score,
            quality_flag="OK",
            config_version="v1",
        )
        await db.execute(insert_stmt)
    await db.commit()

# Helper for UUID
from uuid import uuid4
