import math
import os
from uuid import uuid4
from typing import List

from sqlalchemy import select, func, insert
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from ..models import Incident, SilentZoneScore
from shapely.geometry import Polygon, mapping

# Configuration: tile size in degrees (~1 km at equator)
TILE_SIZE_DEG = float(os.getenv("TILE_SIZE_DEG", "0.01"))


async def _aggregate_reports(db: AsyncSession) -> List[dict]:
    """Aggregate observed incident counts per tile using Incident.lat / Incident.lon.

    Works on plain SQLite — no PostGIS functions needed.
    Returns a list of dicts: {"tile_id": str, "observed": int, "geom": Polygon}
    """
    # Fetch all completed incidents that have coordinates
    stmt = (
        select(Incident.lat, Incident.lon)
        .where(Incident.lat.isnot(None))
        .where(Incident.lon.isnot(None))
    )
    result = await db.execute(stmt)
    rows = result.fetchall()

    # Bin into tiles in Python
    tile_counts: dict[str, int] = {}
    for row in rows:
        lat, lon = row.lat, row.lon
        tile_x = math.floor(lon / TILE_SIZE_DEG) * TILE_SIZE_DEG
        tile_y = math.floor(lat / TILE_SIZE_DEG) * TILE_SIZE_DEG
        tile_id = f"{tile_x}_{tile_y}"
        tile_counts[tile_id] = tile_counts.get(tile_id, 0) + 1

    tiles = []
    for tile_id, observed in tile_counts.items():
        parts = tile_id.split("_")
        tile_x = float(parts[0])
        tile_y = float(parts[1])
        poly = Polygon([
            (tile_x, tile_y),
            (tile_x + TILE_SIZE_DEG, tile_y),
            (tile_x + TILE_SIZE_DEG, tile_y + TILE_SIZE_DEG),
            (tile_x, tile_y + TILE_SIZE_DEG),
            (tile_x, tile_y),
        ])
        tiles.append({"tile_id": tile_id, "observed": observed, "geom": poly})

    return tiles


async def compute_silent_zones(db: AsyncSession):
    """Compute silent-zone scores for each tile and store them.

    Uses the corrected formula from section 6.3:
        silence_factor = 1 / (1 + observed / max(expected, epsilon))
        final_score = exposure * vulnerability * silence_factor * (1 + lambda_ * connectivity_penalty)

    For demo purposes, exposure, vulnerability, expected, and connectivity_penalty
    are stubbed with deterministic values.
    """
    epsilon = 1e-6
    lambda_ = 0.5

    # Step 1: aggregate observed incidents per tile
    tiles = await _aggregate_reports(db)
    logger.info(f"Silent zones: found {len(tiles)} tiles with incidents")

    for tile in tiles:
        observed = tile["observed"]
        # Stubbed values — could be based on population density, hazard maps, etc.
        expected = 5.0
        exposure = 1.0
        vulnerability = 1.0
        connectivity_penalty = 0.0

        silence_factor = 1.0 / (1.0 + observed / max(expected, epsilon))
        final_score = (
            exposure * vulnerability * silence_factor * (1.0 + lambda_ * connectivity_penalty)
        )

        # Insert into SilentZoneScore table
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
    logger.success(f"Silent zones: computed and stored {len(tiles)} tile scores")
