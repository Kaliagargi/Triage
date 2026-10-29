# System Design: Indic Speech-to-Geospatial Disaster Response Platform (TRACE-India)

**Version:** 1.0 draft | **Scope:** backend, frontend, AI layer, database layer, and their integration

## 1. Purpose and Scope

TRACE-India converts distress voice reports (calls, WhatsApp voice notes, radio) in Indian languages into structured, geolocated incident records, and separately detects **silent zones**: areas with high hazard and population but unusually few reports. Output is a live operations map for SDMA/NDRF/district control rooms.

**In scope:** ingestion, ASR, extraction, vernacular geocoding, silent-zone scoring, dispatch dashboard, audit and security.

**Out of scope (v1):** automated dispatch without human approval, drone flight control, citizen-facing app.

**Design principle:** the AI proposes, a human operator confirms. Every AI output carries a confidence score, and low-confidence items are routed to a review queue, never silently dropped or auto-dispatched.

## 2. Requirements

### 2.1 Functional

- Accept audio from telephony (IVR/missed-call callback), WhatsApp Business voice notes, and uploaded radio clips.
- Transcribe code-mixed speech (Hinglish, Tanglish, Bengali-English, Odia, Assamese, etc.) with language ID.
- Extract urgency, needs, landmarks, and administrative locations.
- Resolve each report to a point or polygon with a confidence radius.
- Deduplicate reports describing the same incident.
- Compute silent-zone priority per grid cell and flag Class-1 Dark Zones.
- Provide a live map, triage queue, incident detail view, and audit trail.

### 2.2 Non-functional (initial targets)

| Attribute | Target |
| --- | --- |
| End-to-end latency (audio received to map pin) | P50 < 45 s, P95 < 2 min for 60 s audio |
| Silent-zone refresh | Every 10 min (configurable) |
| Availability | 99.9% for ingestion; dashboard degraded-mode tolerant |
| Peak load | 500 concurrent calls, 5,000 voice notes/hour (burst) |
| Data residency | All data and models hosted in India (MeitY-empanelled cloud or NIC/State Data Centre) |
| Auditability | Every transcript, extraction, and geocode traceable to model version and operator action |

## 3. High-Level Architecture

```
[Phone/IVR] [WhatsApp] [Radio/Upload]
      \          |          /
       v         v         v
  +-----------------------------+
  |  Ingestion Gateway (API)    |  webhook verify, dedupe, store raw audio
  +--------------+--------------+
                 v
        [Message Bus: Kafka / Redpanda]
   topics: audio.raw, audio.clean, text.transcribed,
           entities.extracted, geo.resolved, incidents.updated
                 |
   +-------------+-------------------------------+
   v             v                               v
[Audio Prep] [AI Pipeline Workers]        [Silent-Zone Engine]
             ASR > LID > NER > Geocode     hazard + census + calls
                 |                               |
                 v                               v
        +-------------------------------------------------+
        | PostgreSQL + PostGIS | Object Store | Redis | TS  |
        +----------------------+--------------------------+
                 ^                               ^
                 |                               |
        [Core API (REST + WebSocket/SSE)]   [Tile Server]
                 ^                               ^
                 +---------- Web Dashboard ------+
```

The system is **event-driven**: each stage consumes one topic and produces the next, so stages scale and fail independently and any stage can be replayed with a newer model.

## 4. Ingestion Layer

### 4.1 Channels

| Channel | Mechanism | Notes |
| --- | --- | --- |
| Voice call | Exotel/Twilio India webhook, call recording callback | 8 kHz mono G.711; capture caller number (hashed for analytics, raw encrypted for dispatch callback) |
| WhatsApp | WhatsApp Business Cloud API webhook; fetch media by media ID | OGG/Opus 16 kHz; also captures shared location pins, which are ground truth and skip geocoding |
| Radio / uploads | Operator upload or stream chunker | Segment with VAD into 30-60 s chunks |

### 4.2 Ingestion Gateway responsibilities

1. Verify webhook signatures; reject replays (idempotency key = provider message ID).
2. Write raw audio to object storage (S3-compatible, encrypted, immutable).
3. Insert a `reports` row with status `RECEIVED`.
4. Publish `audio.raw` with report ID, channel, received time, and any native location (WhatsApp pin, cell-tower ID from telco if available).
5. Return 200 immediately; never run AI inline in the webhook.

### 4.3 Audio preprocessing worker

- Convert to 16 kHz mono WAV (torchaudio/ffmpeg); upsample 8 kHz telephony audio.
- Voice activity detection (Silero VAD) and segmentation.
- Light denoising only; heavy denoising can hurt ASR on flood or wind noise, so keep the original and run ASR on both when SNR is low.
- Publish `audio.clean`.

## 5. AI Layer

### 5.1 Serving topology

- Models run as stateless GPU services (Triton Inference Server or vLLM for the LLM), behind an internal gRPC interface.
- Pipeline workers are thin orchestrators: consume a topic, call model services, write results to DB, publish next topic.
- Every model call records `model_name`, `model_version`, and `latency_ms` in `ai_runs` for audit and A/B comparison.
- A **fallback chain** is configured per stage (for example, self-hosted IndicWhisper first, Bhashini Dhruva ASR as secondary, or the reverse in low-GPU deployments).

### 5.2 Stage A: ASR and language ID

- Primary: AI4Bharat IndicWhisper (self-hosted). Secondary: Bhashini Dhruva ASR API.
- Run LID on the first 10-15 s; allow per-segment LID since callers switch languages mid-call.
- Output: segments with timestamps, text in original script, per-segment confidence, detected language(s), and a code-mix flag.
- Store the original-script transcript as the source of truth. Normalized or translated variants are derived fields.

### 5.3 Stage B: Normalization

- IndicXlit to produce a romanized/standardized form for matching place names across scripts (for example, Devanagari and Roman spellings of the same village).
- IndicTrans2 to produce an English working copy for dashboard display and for LLM fallback. Never overwrite the original.

### 5.4 Stage C: Information extraction

Two-model approach for robustness:

1. **Token-level NER** (IndicBERTv2 fine-tuned): tags `URGENCY`, `NEEDS`, `LOC_LANDMARK`, `LOC_ADMIN`, plus recommended extras: `PEOPLE_COUNT`, `VULNERABLE_GROUP` (elderly, pregnant, disabled, infant), `HAZARD_TYPE`, and `RELATION` words (behind, near, opposite, after).
2. **Instruction-tuned LLM** (for example Llama-3.1-8B-Instruct, quantized) with a strict JSON schema output, used to resolve relational phrases (Mandir ke peeche, puliya ke paas) into structured triples: `{anchor: kali mandir, relation: behind, secondary_anchor: bridge}`.

Agreement between the two raises confidence; disagreement sends the report to human review. Validate all LLM output against a JSON Schema and reject malformed output.

**Training data:** start with a few thousand hand-labelled transcripts per priority language (use NDMA, SDMA helpline archives and simulated calls recorded by native speakers), and use active learning from operator corrections in production.

### 5.5 Stage D: Vernacular geocoding

A tiered resolver, from coarse to fine:

1. **Admin resolution:** match `LOC_ADMIN` strings against LGD (state, district, block/tehsil/taluk, gram panchayat, village codes) using fuzzy match over transliterated names (trigram + phonetic keys). Result: an admin polygon.
2. **Landmark resolution inside the admin polygon:** query a local OSM extract (PostGIS, not public Nominatim) for features matching the landmark type and name (temple, ration shop, panchayat bhawan, railway crossing, school, culvert). Local names are often missing, so maintain a **gazetteer table** with aliases (kali mandir, kali bari, Kali temple) that grows from operator confirmations.
3. **Spatial relation reasoning:** apply relations to anchors (behind and near become buffers with a heading-agnostic radius; between two anchors becomes the intersection of buffers).
4. **Scoring:** output a point plus uncertainty radius, plus a ranked candidate list. Confidence combines admin match score, anchor uniqueness within the polygon, number of corroborating anchors, and ASR confidence.
5. **Fallbacks:** native location (WhatsApp pin, telco cell ID centroid) overrides when present; otherwise the admin polygon centroid is stored with a large radius and a `COARSE` flag.

Self-host Nominatim/Overpass or, better, query PostGIS directly to avoid public API rate limits and keep data in-country.

### 5.6 Stage E: Dedup and triage

- Cluster reports that are within the combined uncertainty radius, within a time window, and similar in extracted needs (embedding similarity on the English working copy). Merge into one **incident** with a report count.
- Triage score from urgency keywords, vulnerable-group flags, and water-level phrases; combined with corroboration count.
- Output to `incidents` and publish `incidents.updated`.

### 5.7 Quality and safety controls

- Confidence thresholds per stage; below threshold goes to the human review queue.
- Prompt injection hygiene: transcripts are untrusted input; the LLM never has tool access and only emits schema-bound JSON.
- Evaluation: weekly benchmark on a frozen labelled set (WER/CER per language, NER F1, geocode error in metres at P50/P90, percent within 500 m). Gate model upgrades on non-regression.

## 6. Silent-Zone Engine

### 6.1 Spatial unit

Use a hexagonal grid (H3 resolution 8, roughly 0.7 km2) for dense areas and village polygons (LGD) for rural areas, mapped to each other through a lookup table.

### 6.2 Inputs

| Input | Source | Refresh |
| --- | --- | --- |
| Flood extent / inundation | NRSC Disaster Support Centre, Bhuvan, Sentinel-1 derived layers | Per satellite pass, typically 6-24 h |
| Rainfall and river gauges | IMD, CWC | 15-60 min |
| Population density, kutcha housing ratio, age dependency | Census, SECC, WorldPop as supplement | Static, preprocessed |
| Infrastructure status | Cell tower outage lists (if telco partnership), power grid outage feeds from DISCOM | As available |
| Incoming reports | Internal `incidents` and `reports` | Streaming |

### 6.3 Priority score (with corrections)

The original formula divides by Log(calls + 1), which is **zero when calls = 0** and therefore breaks exactly in the case you care about. Use a bounded, normalized form instead:

```
Exposure   = hazard_exposure (0-1)
Vulnerab.  = normalized(pop_density) * (1 + w1*kutcha_ratio + w2*age_dependency)
Expected   = k * population * report_propensity      # historic calls per capita for this district type
Silence    = 1 / (1 + observed_reports / max(Expected, epsilon))
Priority   = Exposure * Vulnerab. * Silence * ConnectivityPenaltyBoost
```

Notes:

- Dividing by expected reports, not raw calls, avoids flagging a sparse village merely because few people live there, and avoids over-flagging dense cities that always call more.
- `ConnectivityPenaltyBoost` (1.0-1.5) rises when tower or power outages are confirmed, since silence then has a mundane explanation that still demands rescue.
- Weights are initial; calibrate against past events (Assam 2022, Odisha Fani/Yaas, Chennai 2015/2023) by checking whether the score would have ranked later-confirmed high-need areas highly.
- Report both the score and its components in the UI so operators can see why a zone was flagged.

### 6.4 Classification

| Class | Meaning | Default action |
| --- | --- | --- |
| 1 Dark Zone | High priority, near-zero reports, hazard confirmed | Recommend NDRF/SDRF or drone recon; operator approval required |
| 2 Under-reported | High priority, reports well below expected | Recommend callback sweep, local officials (patwari, BDO) contact |
| 3 Watch | Moderate priority | Monitor |

Thresholds are configurable per event. Each flag includes a **freshness stamp** because satellite extents may be hours old.

### 6.5 Execution

A scheduled job (Airflow or a cron-driven worker) recomputes scores in PostGIS using SQL over materialized views, writes to `silent_zone_scores`, and publishes `zones.updated`. Heavy raster operations (flood-extent to cells) run once per new satellite layer using rasterio/GeoPandas and are cached.

## 7. Backend Services

| Service | Responsibility | Tech |
| --- | --- | --- |
| Ingestion Gateway | Webhooks, validation, raw storage | FastAPI, behind API gateway |
| Audio Prep Worker | Resample, VAD, segmentation | Python, torchaudio |
| AI Pipeline Workers | ASR, LID, NER, geocode, dedupe | Python, Kafka consumers, gRPC clients |
| Model Serving | GPU inference | Triton / vLLM |
| Silent-Zone Engine | Scoring and classification | Python, PostGIS SQL, Airflow |
| Core API | CRUD, auth, queries, realtime push | FastAPI (REST) + WebSocket/SSE |
| Tile/Feature Server | Vector tiles for map layers | pg\_tileserv or Martin |
| Notification Service | SMS/WhatsApp/email to field teams, callback IVR | Exotel, WhatsApp API |
| Admin / Gazetteer Service | Alias management, model config, thresholds | FastAPI |

### 7.1 Core API (representative endpoints)

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/incidents?bbox=&status=&min_priority=` | Filtered incident list for map and queue |
| `GET /v1/incidents/{id}` | Detail: transcript, entities, geocode candidates, audio URL (signed) |
| `PATCH /v1/incidents/{id}` | Operator edits: correct location, status, assignment (writes audit row and training label) |
| `POST /v1/incidents/{id}/dispatch` | Create dispatch task for a team |
| `GET /v1/zones?event_id=&class=` | Silent-zone cells and components |
| `POST /v1/zones/{id}/actions` | Approve recon deployment, mark verified |
| `GET /v1/review-queue` | Low-confidence items |
| `GET /v1/stream` (SSE/WebSocket) | Push of `incidents.updated` and `zones.updated` |
| `POST /v1/gazetteer/aliases` | Add local landmark alias |

### 7.2 Cross-cutting

- AuthN/Z: OIDC (state SSO or Keycloak), RBAC roles: Operator, Supervisor, Field Team, Analyst, Admin. Scope data by district.
- Idempotency keys on all POST paths; at-least-once delivery from Kafka with idempotent consumers.
- Rate limiting and WAF at the edge.
- Structured logging with trace IDs spanning webhook to dashboard.

## 8. Database Layer

### 8.1 Stores

| Store | Use |
| --- | --- |
| PostgreSQL 16 + PostGIS | System of record: reports, incidents, zones, gazetteer, boundaries |
| Object storage (S3-compatible) | Raw and cleaned audio, satellite rasters, model artifacts |
| Redis | Hot cache of incident list per district, session state, pub/sub for dashboard fan-out |
| Kafka | Event log with 7-30 day retention for replay |
| TimescaleDB (or Postgres partitions) | Call-volume time series, model metrics |
| Optional: OpenSearch | Full-text search across transcripts in multiple scripts |

### 8.2 Core schema (simplified)

```sql
CREATE TABLE reports (
  id UUID PRIMARY KEY,
  event_id UUID NOT NULL,
  channel TEXT NOT NULL,              -- IVR | WHATSAPP | RADIO | UPLOAD
  received_at TIMESTAMPTZ NOT NULL,
  caller_hash TEXT,                   -- salted hash for dedup/analytics
  caller_enc BYTEA,                   -- encrypted, access-logged, for callback only
  audio_uri TEXT NOT NULL,
  native_geom GEOGRAPHY(Point,4326),  -- WhatsApp pin or telco centroid
  status TEXT NOT NULL,               -- RECEIVED|TRANSCRIBED|EXTRACTED|GEOCODED|REVIEW|DONE
  incident_id UUID
);

CREATE TABLE transcripts (
  id UUID PRIMARY KEY,
  report_id UUID REFERENCES reports(id),
  lang_codes TEXT[], code_mixed BOOLEAN,
  text_original TEXT, text_roman TEXT, text_en TEXT,
  asr_confidence REAL, model_version TEXT
);

CREATE TABLE entities (
  id UUID PRIMARY KEY,
  report_id UUID REFERENCES reports(id),
  label TEXT, text TEXT, span INT4RANGE, confidence REAL, model_version TEXT
);

CREATE TABLE incidents (
  id UUID PRIMARY KEY,
  event_id UUID NOT NULL,
  geom GEOGRAPHY(Point,4326),
  radius_m REAL,
  geo_confidence REAL, geo_method TEXT,   -- NATIVE|LANDMARK|ADMIN_CENTROID
  admin_lgd_code TEXT,
  needs TEXT[], urgency SMALLINT, people_count INT,
  report_count INT, triage_score REAL,
  status TEXT, assigned_team UUID,
  updated_at TIMESTAMPTZ
);
CREATE INDEX ON incidents USING GIST (geom);

CREATE TABLE gazetteer (
  id UUID PRIMARY KEY,
  name TEXT, name_roman TEXT, aliases TEXT[],
  feature_type TEXT, osm_id BIGINT, admin_lgd_code TEXT,
  geom GEOMETRY(Geometry,4326), source TEXT, verified BOOLEAN
);
CREATE INDEX ON gazetteer USING GIST (geom);
CREATE INDEX ON gazetteer USING GIN (aliases);

CREATE TABLE admin_boundaries (
  lgd_code TEXT PRIMARY KEY, level TEXT, name TEXT, name_roman TEXT,
  parent_code TEXT, geom GEOMETRY(MultiPolygon,4326)
);

CREATE TABLE silent_zone_scores (
  event_id UUID, cell_id TEXT, computed_at TIMESTAMPTZ,
  hazard REAL, vulnerability REAL, expected_reports REAL,
  observed_reports INT, priority REAL, class SMALLINT,
  hazard_source_ts TIMESTAMPTZ,
  PRIMARY KEY (event_id, cell_id, computed_at)
);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY, actor TEXT, action TEXT,
  entity TEXT, entity_id UUID, before JSONB, after JSONB, at TIMESTAMPTZ
);
```

### 8.3 Data practices

- Pre-load LGD boundaries, OSM India extract, and census layers into PostGIS before any event; refresh OSM weekly.
- Partition `reports` and `transcripts` by event and month.
- Operator corrections to location and entities are stored as labelled training data (`feedback` table) and feed the next fine-tuning cycle and gazetteer.
- Retention: raw audio and caller identifiers purged or anonymized per policy after the event closure window (set with the disaster management authority); aggregate data retained.

## 9. Frontend

### 9.1 Application shape

Single-page React (TypeScript) web app for control rooms (desktop first, tablet-usable for field supervisors), with a lighter PWA for field teams.

### 9.2 Main views

1. **Live Map:** Mapbox GL JS or MapLibre GL with deck.gl overlays. Layers: incident pins (colored by triage), uncertainty circles, silent-zone hex grid (Class 1-3), flood extent raster, admin boundaries, team locations. Toggle layers and time slider for hazard snapshots.
2. **Triage Queue:** ranked list synced with map selection, with filters for language, need type, and confidence.
3. **Incident Detail Panel:** audio player with synced transcript (original script plus English), highlighted entities, geocode candidates on mini-map, and one-click confirm/correct location. Edits write back and become training labels.
4. **Review Queue:** low-confidence items for bilingual operators.
5. **Silent-Zone Panel:** cell details showing score components, hazard timestamp, recommended action, and approve-deployment button.
6. **Dispatch Board:** tasks, teams, ETA, status.
7. **Admin:** gazetteer alias editor, thresholds, model versions, system health.

### 9.3 Frontend practices

- Use MapLibre (open source) if Mapbox licensing or in-country hosting is a concern; Leaflet is fine for the lightweight PWA.
- Vector tiles from the tile server for boundaries and grid; GeoJSON only for live incidents (viewport-limited).
- Real-time updates via SSE or WebSocket with automatic reconnect and resync by `updated_since` cursor.
- Unicode and font support for all 22 scheduled languages; right-to-left support for Urdu.
- Low-bandwidth mode: cluster pins, disable rasters, and cache the last state offline (service worker) because control rooms and field teams often have degraded connectivity.
- Accessibility: color-blind-safe palette for priority classes; keyboard shortcuts for operator throughput.

## 10. Integration Flows

### 10.1 Report to map pin

1. Provider webhook hits the Ingestion Gateway; audio saved; `reports` row created; `audio.raw` published.
2. Audio Prep Worker publishes `audio.clean`.
3. ASR Worker calls ASR service; writes `transcripts`; publishes `text.transcribed`.
4. Extraction Worker runs NER and LLM; writes `entities`; publishes `entities.extracted`.
5. Geocode Worker queries PostGIS (LGD, gazetteer, OSM); writes candidates; publishes `geo.resolved`.
6. Dedup/Triage Worker creates or updates `incidents`; publishes `incidents.updated`.
7. Core API consumes the event, updates Redis, and pushes to connected dashboards. Total target: under 2 minutes at P95.

### 10.2 Operator correction loop

Operator fixes a location in the dashboard, then `PATCH /incidents/{id}`, then an audit row and `feedback` row are written, the gazetteer alias is optionally added, and the corrected label is queued for the next evaluation and fine-tuning batch.

### 10.3 Silent-zone loop

New hazard layer or timer triggers the Silent-Zone Engine, which joins hazard, vulnerability, and report counts in PostGIS and writes `silent_zone_scores`. It then publishes `zones.updated`, the dashboard refreshes the hex layer, and a Class-1 flag creates a pending recommendation that a supervisor approves before any notification to NDRF/SDRF.

## 11. Deployment and Operations

- **Infrastructure:** Kubernetes in an India-region cloud or State Data Centre; GPU node pool for models (autoscaled by queue lag); CPU pools for workers. Infrastructure as code (Terraform, Helm).
- **Environments:** dev, staging with replayed historical events, production. Run quarterly drills with simulated floods and recorded mock calls.
- **Graceful degradation:** if GPU capacity or models fail, fall back to Bhashini API; if ASR fails entirely, route audio straight to a human transcription queue so calls are never lost. If the bus lags, ingestion still stores audio.
- **Observability:** Prometheus/Grafana for queue lag, per-stage latency, GPU utilization, WER proxies; OpenTelemetry tracing; alerting on silent failures (for example, sudden drop in ingested calls, which may itself indicate telephony outage).
- **Disaster resilience of the platform itself:** multi-AZ database with streaming replica in a second region, tested restore, and a minimal offline-capable mode for the dashboard.

## 12. Security, Privacy, and Compliance

- Comply with the DPDP Act 2023 (purpose limitation, consent where applicable, retention limits, with the state-function exemptions reviewed by counsel) and CERT-In guidelines.
- Encrypt in transit (TLS 1.3) and at rest (KMS-managed keys); caller numbers in a separate encrypted column with access logging.
- Signed, short-lived URLs for audio; role-based and district-scoped access; full audit log.
- Voice is biometric-adjacent: do not use recordings for model training without a documented legal basis and, where possible, anonymization.
- Bias monitoring: report ASR and geocoding accuracy by language, dialect, gender, and region; silent-zone logic specifically checks that marginalized hamlets (dalit basti, tribal hamlets) that are often missing from OSM are covered through LGD and census data.

## 13. Key Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Poor ASR on dialects and noisy audio | Dual ASR providers, per-language thresholds, human transcription fallback, active learning |
| Landmarks missing from OSM | Operator-grown gazetteer, panchayat-level field mapping before monsoon season, LGD fallback |
| Stale satellite data causing false Dark Zones | Show hazard timestamps, blend rainfall/gauge data, require human approval |
| Silence has benign causes (no one there, evacuated) | Cross-check with evacuation lists and shelter registers before recon |
| Over-trust in AI | Confidence display, review queues, no auto-dispatch in v1 |
| Telephony/cloud outage during the very event | Multi-provider telephony, replica region, offline-capable dashboard |

## 14. Phased Rollout

| Phase | Duration | Deliverable |
| --- | --- | --- |
| 0 | 4-6 weeks | Data prep: LGD, OSM extract, census in PostGIS; annotation guidelines; 2 pilot languages (for example Hindi/Hinglish and Odia, or Tamil/Tanglish) |
| 1 | 8-10 weeks | Ingestion, ASR, NER, geocoding, basic map and triage queue; offline evaluation |
| 2 | 6-8 weeks | Dedup, review queue, operator feedback loop, gazetteer tooling |
| 3 | 6-8 weeks | Silent-zone engine with historical event calibration; dashboard layers |
| 4 | 4-6 weeks | Hardening, security audit, drills, SDMA pilot in one district |
| 5 | Ongoing | Add languages, telco/DISCOM outage feeds, drone tasking integration |

## 15. Open Questions

- Which authority owns the data and operates the system (SDMA, district, NDMA)? This decides hosting and legal basis.
- Is a telco partnership available for cell-tower outage and coarse location data?
- Which languages and districts are first, and is there access to historical helpline recordings for evaluation?
- Acceptable operator workload: how many reports per operator-hour should the review queue assume?
