# seed_demo.py
import os
import asyncio
import random
import uuid
from datetime import datetime
from shapely.geometry import Point, Polygon, mapping
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Adjust import path to backend package
from backend.models import Base, Report, Incident, Gazetteer, AdminArea, HazardArea
from backend.db import DATABASE_URL  # assume defined in db.py or env

print(f"[DEBUG] Using DATABASE_URL: {DATABASE_URL}")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def create_admin_area(session: AsyncSession):
    # Simple square polygon for demo district
    poly = Polygon([ (77.0, 28.0), (78.0, 28.0), (78.0, 29.0), (77.0, 29.0), (77.0, 28.0) ])
    admin = AdminArea(
        id="demo_district",
        name="Demo District",
        boundary=mapping(poly),
        source="synthetic",
        is_synthetic=True,
    )
    session.add(admin)
    await session.flush()
    return admin

async def create_hazard_area(session: AsyncSession):
    # Simple flood polygon intersecting district
    flood = Polygon([ (77.2, 28.2), (77.8, 28.2), (77.8, 28.8), (77.2, 28.8), (77.2, 28.2) ])
    hazard = HazardArea(
        id=str(uuid.uuid4()),
        hazard_type="flood",
        geometry=mapping(flood),
        source="synthetic",
        source_date=datetime.utcnow(),
        is_illustrative=True,
    )
    session.add(hazard)
    await session.flush()
    return hazard

async def create_gazetteer(session: AsyncSession):
    places = [
        {"name": "mandir", "aliases": ["temple"], "point": Point(77.5, 28.5)},
        {"name": "puliya", "aliases": ["bridge"], "point": Point(77.6, 28.55)},
        {"name": "school", "aliases": [], "point": Point(77.4, 28.45)},
    ]
    for p in places:
        gaz = Gazetteer(
            id=str(uuid.uuid4()),
            name=p["name"],
            normalized_name=p["name"].lower(),
            aliases=p["aliases"],
            feature_type="landmark",
            admin_area_id="demo_district",
            location=mapping(p["point"]),
            source="synthetic",
            verified_for_demo=True,
        )
        session.add(gaz)
    await session.flush()

async def create_synthetic_reports(session: AsyncSession, count: int = 30):
    for _ in range(count):
        # Random point inside district bounds
        lon = random.uniform(77.0, 78.0)
        lat = random.uniform(28.0, 29.0)
        report = Report(
            id=str(uuid.uuid4()),
            audio_path="synthetic.wav",
            audio_sha256="synthetic",
            fixture_id=None,
            source_channel="demo",
            status="completed",
            transcript_text="synthetic transcript",
            transcript_mode="CACHED",
            extraction={},
            is_synthetic=True,
        )
        session.add(report)
        # Create a minimal incident linked to this report
        incident = Incident(
            id=str(uuid.uuid4()),
            report_id=report.id,
            hazard_type="flood",
            urgency="low",
            place_phrase_original="synthetic report",
            location=mapping(Point(lon, lat)),
            approximate_area=None,
            lat=lat,
            lon=lon,
            extraction_confidence=0.75,
            location_confidence=0.75,
            review_status="needs_review",
            is_synthetic=True,
        )
        session.add(incident)
    await session.flush()

async def main():
    async with AsyncSessionLocal() as session:
        # Create missing tables without deleting real or previously reviewed data.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        existing = await session.execute(select(AdminArea).where(AdminArea.id == "demo_district"))
        if existing.scalar_one_or_none():
            print("[INFO] Demo seed already exists; leaving database unchanged.")
            await engine.dispose()
            return
        await create_admin_area(session)
        await create_hazard_area(session)
        await create_gazetteer(session)
        await create_synthetic_reports(session, 30)
        await session.commit()
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
