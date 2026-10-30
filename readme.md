trace-india/
├── docker-compose.yml
├── requirements.txt
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── main.py                  # Core API & Ingestion Gateway
│   ├── services/
│   │   ├── __init__.py
│   │   ├── audio.py             # VAD, resample, ffmpeg/torchaudio
│   │   ├── ai_pipeline.py       # ASR, NER, LLM extraction wrapper
│   │   ├── geocoder.py          # PostGIS tiered spatial resolution
│   │   └── silent_zone.py       # H3 / Grid priority scoring engine
│   ├── workers/
│   │   ├── __init__.py
│   │   └── kafka_consumers.py   # Event bus orchestrators
│   └── frontend/                # React Vite dashboard (MapLibre + Tailwind)
└── db/
    └── init.sql                 # PostGIS extensions & core schema