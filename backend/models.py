from sqlalchemy import Column, String, Text, DateTime, Float, Boolean, JSON, func, Integer, ForeignKey
# UUID import removed for SQLite compatibility
import uuid
from .db import Base

class Report(Base):
    __tablename__ = "reports"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    audio_path = Column(String, nullable=False)
    audio_sha256 = Column(String, nullable=False)
    fixture_id = Column(String, nullable=True)
    source_channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    transcript_text = Column(Text, nullable=True)
    transcript_language = Column(String, nullable=True)
    transcript_mode = Column(String, nullable=True)  # LIVE or CACHED
    transcription_error = Column(Text, nullable=True)
    extraction = Column(JSON, nullable=True)
    is_synthetic = Column(Boolean, default=False)  # synthetic background reports

class Incident(Base):
    __tablename__ = "incidents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    report_id = Column(String, ForeignKey("reports.id"), nullable=False)
    hazard_type = Column(String, nullable=True)
    urgency = Column(String, nullable=True)
    water_level_description = Column(String, nullable=True)
    needs = Column(JSON, nullable=True)  # list of strings
    place_phrase_original = Column(String, nullable=True)
    anchor_name = Column(String, nullable=True)
    spatial_relation = Column(String, nullable=True)
    location = Column(JSON, nullable=True)  # GeoJSON point
    approximate_area = Column(JSON, nullable=True)  # GeoJSON polygon
    location_confidence = Column(Float, nullable=True)
    extraction_confidence = Column(Float, nullable=True)
    review_status = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Gazetteer(Base):
    __tablename__ = "gazetteer"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=False)
    aliases = Column(JSON, nullable=True)  # list of strings
    feature_type = Column(String, nullable=True)
    admin_area_id = Column(String, nullable=True)
    location = Column(JSON, nullable=False)  # GeoJSON point
    source = Column(String, nullable=True)
    verified_for_demo = Column(Boolean, default=False)

class AdminArea(Base):
    __tablename__ = "admin_areas"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    boundary = Column(JSON, nullable=False)  # GeoJSON polygon
    source = Column(String, nullable=True)
    is_synthetic = Column(Boolean, default=False)

class HazardArea(Base):
    __tablename__ = "hazard_areas"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    hazard_type = Column(String, nullable=False)
    geometry = Column(JSON, nullable=False)  # GeoJSON polygon
    source = Column(String, nullable=True)
    source_date = Column(DateTime, nullable=True)
    is_illustrative = Column(Boolean, default=True)

class SilentZoneScore(Base):
    __tablename__ = "silent_zone_scores"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    area_id = Column(String, nullable=False)
    run_at = Column(DateTime(timezone=True), server_default=func.now())
    exposure = Column(Float, nullable=True)
    vulnerability = Column(Float, nullable=True)
    observed_reports = Column(Integer, nullable=True)
    expected_reports = Column(Float, nullable=True)
    connectivity_penalty = Column(Float, nullable=True)
    silence_factor = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)
    quality_flag = Column(String, nullable=True)
    config_version = Column(String, nullable=True)
