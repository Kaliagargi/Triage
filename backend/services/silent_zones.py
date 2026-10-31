"""Grid-based silent-zone analysis for the current geolocated report footprint.

The grid deliberately includes cells with zero reports. Expected report counts,
exposure and vulnerability are configurable demo baselines until real population,
hazard-exposure and connectivity layers are supplied. Results are therefore
labelled ILLUSTRATIVE_BASELINE and must not be interpreted as measured risk.
"""
import math
import os
import json
from typing import Dict, List
from uuid import uuid4

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from shapely.geometry import Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..models import Incident, SilentZoneScore, HazardArea

TILE_SIZE_DEG = float(os.getenv("TILE_SIZE_DEG", "0.05"))
EXPECTED_REPORTS_PER_TILE = float(os.getenv("EXPECTED_REPORTS_PER_TILE", "2.0"))
EXPOSURE_BASELINE = float(os.getenv("SILENT_ZONE_EXPOSURE", "1.0"))
VULNERABILITY_BASELINE = float(os.getenv("SILENT_ZONE_VULNERABILITY", "1.0"))
CONNECTIVITY_PENALTY = float(os.getenv("SILENT_ZONE_CONNECTIVITY_PENALTY", "0.0"))
MAX_GRID_CELLS = int(os.getenv("MAX_SILENT_ZONE_CELLS", "2500"))
LAMBDA = 0.5


def tile_id(ix: int, iy: int) -> str:
    """Stable integer grid key; avoids ambiguous negative decimal parsing."""
    return f"{ix}:{iy}"


def tile_polygon(ix: int, iy: int) -> Polygon:
    west = ix * TILE_SIZE_DEG
    south = iy * TILE_SIZE_DEG
    east = west + TILE_SIZE_DEG
    north = south + TILE_SIZE_DEG
    return Polygon([(west, south), (east, south), (east, north), (west, north), (west, south)])


def polygon_for_area_id(area_id: str) -> Polygon:
    ix_text, iy_text = area_id.split(":", 1)
    return tile_polygon(int(ix_text), int(iy_text))


def calculate_silence_score(
    observed: int,
    expected: float = EXPECTED_REPORTS_PER_TILE,
    exposure: float = EXPOSURE_BASELINE,
    vulnerability: float = VULNERABILITY_BASELINE,
    connectivity_penalty: float = CONNECTIVITY_PENALTY,
) -> Dict[str, float]:
    """Return the documented score components for one grid cell."""
    if expected <= 0:
        raise ValueError("expected report baseline must be greater than zero")
    if observed < 0:
        raise ValueError("observed report count cannot be negative")
    if not 0 <= exposure <= 1 or not 0 <= vulnerability <= 1:
        raise ValueError("exposure and vulnerability baselines must be between 0 and 1")
    if not 0 <= connectivity_penalty <= 1:
        raise ValueError("connectivity penalty must be between 0 and 1")

    silence_factor = 1.0 / (1.0 + observed / expected)
    final_score = exposure * vulnerability * silence_factor * (1.0 + LAMBDA * connectivity_penalty)
    return {
        "silence_factor": silence_factor,
        "final_score": final_score,
        "expected_reports": expected,
        "exposure": exposure,
        "vulnerability": vulnerability,
        "connectivity_penalty": connectivity_penalty,
    }


async def _load_geolocated_incidents(db: AsyncSession) -> List[tuple]:
    result = await db.execute(
        select(Incident.lat, Incident.lon)
        .where(Incident.lat.isnot(None))
        .where(Incident.lon.isnot(None))
    )
    return [(float(row.lat), float(row.lon)) for row in result.fetchall()]


def _build_grid(points: List[tuple]) -> List[dict]:
    """Create a complete grid around report coverage, including empty cells."""
    if not points:
        return []
    if TILE_SIZE_DEG <= 0:
        raise ValueError("TILE_SIZE_DEG must be greater than zero")

    min_lat = min(lat for lat, _ in points)
    max_lat = max(lat for lat, _ in points)
    min_lon = min(lon for _, lon in points)
    max_lon = max(lon for _, lon in points)

    min_ix = math.floor(min_lon / TILE_SIZE_DEG) - 1
    max_ix = math.floor(max_lon / TILE_SIZE_DEG) + 1
    min_iy = math.floor(min_lat / TILE_SIZE_DEG) - 1
    max_iy = math.floor(max_lat / TILE_SIZE_DEG) + 1
    cell_count = (max_ix - min_ix + 1) * (max_iy - min_iy + 1)
    if cell_count > MAX_GRID_CELLS:
        raise ValueError(
            f"Report footprint would create {cell_count} grid cells; "
            f"limit is {MAX_GRID_CELLS}. Increase TILE_SIZE_DEG or narrow the demo area."
        )

    counts: Dict[str, int] = {}
    for lat, lon in points:
        key = tile_id(math.floor(lon / TILE_SIZE_DEG), math.floor(lat / TILE_SIZE_DEG))
        counts[key] = counts.get(key, 0) + 1

    cells = []
    for ix in range(min_ix, max_ix + 1):
        for iy in range(min_iy, max_iy + 1):
            key = tile_id(ix, iy)
            cells.append({
                "area_id": key,
                "observed_reports": counts.get(key, 0),
                "geometry": mapping(tile_polygon(ix, iy)),
            })
    return cells


async def _load_hazard_geometry(db: AsyncSession):
    result = await db.execute(
        select(HazardArea.geometry).where(HazardArea.hazard_type == "flood")
    )
    geometries: List[BaseGeometry] = []

    for value in result.scalars().all():
        try:
            if isinstance(value, str):
                value = json.loads(value)

            # Database JSON may contain GeoJSON geometry, a Feature wrapper,
            # or null/legacy values. Normalize to a Shapely geometry and
            # never pass non-geometry objects into unary_union.
            if not isinstance(value, dict):
                logger.warning(
                    "Ignoring hazard geometry with unsupported type: %s",
                    type(value).__name__,
                )
                continue
            if value.get("type") == "Feature":
                value = value.get("geometry")
            if not isinstance(value, dict) or not value.get("type"):
                logger.warning("Ignoring empty or malformed hazard geometry")
                continue

            geometry = shape(value)
            if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
                logger.warning("Ignoring empty hazard geometry")
                continue
            if not geometry.is_valid:
                logger.warning("Ignoring invalid hazard geometry")
                continue
            geometries.append(geometry)
        except Exception:
            # One malformed stored row must not prevent analysis of the rest.
            logger.exception("Ignoring malformed illustrative hazard geometry row")

    if not geometries:
        return None

    # Avoid Shapely's array-based union_all path for a single geometry.
    # Some Shapely/NumPy combinations fail in create_collection even when
    # the input is one valid Polygon; no union is needed in that case.
    if len(geometries) == 1:
        return geometries[0]

    # Combine incrementally instead of passing a Python list through
    # unary_union/union_all (which constructs a NumPy geometry collection).
    combined = geometries[0]
    try:
        for geometry in geometries[1:]:
            combined = combined.union(geometry)
        return combined
    except Exception:
        logger.exception("Could not combine flood hazard geometries")
        # Preserve a valid geometry and keep the demo operational. This
        # fallback is illustrative and may under-represent total hazard area.
        return max(geometries, key=lambda geometry: geometry.area)


def _exposure_for_cell(area_id: str, hazard_geometry) -> float:
    if hazard_geometry is None:
        return EXPOSURE_BASELINE
    cell = polygon_for_area_id(area_id)
    if not cell.area:
        return 0.0
    return max(0.0, min(1.0, cell.intersection(hazard_geometry).area / cell.area))


async def compute_silent_zones(db: AsyncSession) -> dict:
    """Replace the latest snapshot with a full grid, including zero-report cells."""
    points = await _load_geolocated_incidents(db)
    cells = _build_grid(points)
    hazard_geometry = await _load_hazard_geometry(db)

    # The table has no run_id column. Keep one current snapshot rather than
    # mixing cells from different computation runs in the API response.
    await db.execute(delete(SilentZoneScore))

    rows = []
    for cell in cells:
        exposure = _exposure_for_cell(cell["area_id"], hazard_geometry)
        score = calculate_silence_score(cell["observed_reports"], exposure=exposure)
        rows.append(SilentZoneScore(
            id=str(uuid4()),
            area_id=cell["area_id"],
            exposure=score["exposure"],
            vulnerability=score["vulnerability"],
            observed_reports=cell["observed_reports"],
            expected_reports=score["expected_reports"],
            connectivity_penalty=score["connectivity_penalty"],
            silence_factor=score["silence_factor"],
            final_score=score["final_score"],
            quality_flag="ILLUSTRATIVE_BASELINE",
            config_version="grid-v3",
        ))

    if rows:
        db.add_all(rows)
    await db.commit()

    silent_count = sum(1 for cell in cells if cell["observed_reports"] == 0)
    logger.info(
        "Silent-zone snapshot: %s cells, %s with zero reports, %s geolocated reports",
        len(cells), silent_count, len(points),
    )
    return {
        "status": "computed",
        "cell_count": len(cells),
        "silent_cell_count": silent_count,
        "geolocated_report_count": len(points),
        "quality_flag": "ILLUSTRATIVE_BASELINE",
        "config_version": "grid-v3",
    }
