CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE reports (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  event_id UUID NOT NULL,
  channel TEXT NOT NULL,              -- IVR | WHATSAPP | RADIO | UPLOAD
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  caller_hash TEXT,                   -- salted hash for dedup/analytics
  caller_enc BYTEA,                   -- encrypted for callback only
  audio_uri TEXT NOT NULL,
  native_geom GEOGRAPHY(Point,4326),  -- WhatsApp pin or telco centroid
  status TEXT NOT NULL DEFAULT 'RECEIVED', -- RECEIVED|TRANSCRIBED|EXTRACTED|GEOCODED|REVIEW|DONE
  incident_id UUID
);

CREATE TABLE transcripts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  report_id UUID REFERENCES reports(id) ON DELETE CASCADE,
  lang_codes TEXT[], 
  code_mixed BOOLEAN,
  text_original TEXT, 
  text_roman TEXT, 
  text_en TEXT,
  asr_confidence REAL, 
  model_version TEXT
);

CREATE TABLE entities (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  report_id UUID REFERENCES reports(id) ON DELETE CASCADE,
  label TEXT, 
  text TEXT, 
  span INT4RANGE, 
  confidence REAL, 
  model_version TEXT
);

CREATE TABLE incidents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  event_id UUID NOT NULL,
  geom GEOGRAPHY(Point,4326),
  radius_m REAL,
  geo_confidence REAL, 
  geo_method TEXT,   -- NATIVE|LANDMARK|ADMIN_CENTROID
  admin_lgd_code TEXT,
  needs TEXT[], 
  urgency SMALLINT, 
  people_count INT,
  report_count INT DEFAULT 1, 
  triage_score REAL,
  status TEXT DEFAULT 'OPEN', 
  assigned_team UUID,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_incidents_geom ON incidents USING GIST (geom);

CREATE TABLE gazetteer (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT, 
  name_roman TEXT, 
  aliases TEXT[],
  feature_type TEXT, 
  osm_id BIGINT, 
  admin_lgd_code TEXT,
  geom GEOMETRY(Geometry,4326), 
  source TEXT, 
  verified BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_gazetteer_geom ON gazetteer USING GIST (geom);
CREATE INDEX idx_gazetteer_aliases ON gazetteer USING GIN (aliases);

CREATE TABLE admin_boundaries (
  lgd_code TEXT PRIMARY KEY, 
  level TEXT, 
  name TEXT, 
  name_roman TEXT,
  parent_code TEXT, 
  geom GEOMETRY(MultiPolygon,4326)
);
CREATE INDEX idx_admin_geom ON admin_boundaries USING GIST (geom);

CREATE TABLE silent_zone_scores (
  event_id UUID, 
  cell_id TEXT, 
  computed_at TIMESTAMPTZ DEFAULT NOW(),
  hazard REAL, 
  vulnerability REAL, 
  expected_reports REAL,
  observed_reports INT, 
  priority REAL, 
  class SMALLINT,
  hazard_source_ts TIMESTAMPTZ,
  PRIMARY KEY (event_id, cell_id, computed_at)
);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY, 
  actor TEXT, 
  action TEXT,
  entity TEXT, 
  entity_id UUID, 
  before JSONB, 
  after JSONB, 
  at TIMESTAMPTZ DEFAULT NOW()
);