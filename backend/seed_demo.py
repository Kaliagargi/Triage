# seed_demo.py
import asyncio
import json
import random
import uuid
from datetime import datetime, timezone

from shapely.geometry import Point, Polygon, mapping, shape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Report, Incident, Gazetteer, AdminArea, HazardArea
from backend.db import DATABASE_URL

print(f"[DEBUG] Using DATABASE_URL: {DATABASE_URL}")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_admin_area(session: AsyncSession):
    poly = Polygon([(77.0, 28.0), (78.0, 28.0), (78.0, 29.0), (77.0, 29.0), (77.0, 28.0)])
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
    flood = Polygon([(77.2, 28.2), (77.8, 28.2), (77.8, 28.8), (77.2, 28.8), (77.2, 28.2)])
    hazard = HazardArea(
        id=str(uuid.uuid4()),
        hazard_type="flood",
        geometry=mapping(flood),
        source="synthetic",
        source_date=datetime.now(timezone.utc),
        is_illustrative=True,
    )
    session.add(hazard)
    await session.flush()
    return hazard


async def create_gazetteer(session: AsyncSession):
    places = [
        {"name": "mandir", "aliases": ["temple", "kali mandir", "काली मंदिर", "मंदिर"], "point": Point(77.5, 28.5)},
        {"name": "puliya", "aliases": ["bridge", "पुलिया"], "point": Point(77.6, 28.55)},
        {"name": "school", "aliases": ["विद्यालय", "स्कूल"], "point": Point(77.4, 28.45)},
    ]
    for place in places:
        session.add(Gazetteer(
            id=str(uuid.uuid4()),
            name=place["name"],
            normalized_name=place["name"].lower(),
            aliases=place["aliases"],
            feature_type="landmark",
            admin_area_id="demo_district",
            location=mapping(place["point"]),
            source="synthetic",
            verified_for_demo=True,
        ))
    await session.flush()


async def create_synthetic_reports(session: AsyncSession, count: int = 30):
    for _ in range(count):
        lon, lat = random.uniform(77.0, 78.0), random.uniform(28.0, 29.0)
        report_id = str(uuid.uuid4())
        report = Report(
            id=report_id,
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
        session.add(Incident(
            id=str(uuid.uuid4()),
            report_id=report_id,
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
        ))
    await session.flush()


async def repair_existing_demo(session: AsyncSession):
    aliases_by_name = {
        "mandir": ["temple", "kali mandir", "काली मंदिर", "मंदिर"],
        "puliya": ["bridge", "पुलिया"],
        "school": ["विद्यालय", "स्कूल"],
    }
    gazetteer_rows = await session.execute(
        select(Gazetteer).where(Gazetteer.admin_area_id == "demo_district")
    )
    for gaz in gazetteer_rows.scalars():
        gaz.aliases = aliases_by_name.get(gaz.normalized_name, gaz.aliases or [])

    synthetic_rows = await session.execute(
        select(Incident).join(Report, Incident.report_id == Report.id)
        .where(Report.is_synthetic.is_(True))
    )
    for incident in synthetic_rows.scalars():
        geometry = incident.location
        if isinstance(geometry, str):
            try:
                geometry = json.loads(geometry)
            except (json.JSONDecodeError, TypeError):
                geometry = None
        if geometry:
            try:
                point = shape(geometry)
                incident.location = mapping(point)
                if point.geom_type == "Point":
                    incident.lat, incident.lon = point.y, point.x
            except (TypeError, ValueError):
                pass


async def main():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        district = await session.get(AdminArea, "demo_district")
        if district:
            await repair_existing_demo(session)
            await session.commit()
            print("[INFO] Existing demo seed repaired; real reports were preserved.")
        else:
            await create_admin_area(session)
            await create_hazard_area(session)
            await create_gazetteer(session)
            await create_synthetic_reports(session, 30)
            await session.commit()
            print("[INFO] Demo district, hazard, gazetteer, and synthetic reports seeded.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
