# TRACE-India

Community-driven disaster incident reporting and silent-zone analysis demo.

## Docker-free local setup

Docker and Docker Compose are **not used or required**. Run Python/FastAPI and Node/Vite directly on your machine.

### Requirements
- Python 3.12
- Node.js 20+ and npm
- Git

The backend defaults to local SQLite (`sqlite+aiosqlite:///./demo.db`); PostgreSQL/PostGIS is not required for the demo.

### 1. Backend (Windows PowerShell)

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
$env:DATABASE_URL = "sqlite+aiosqlite:///./demo.db"
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

If PowerShell blocks activation, run the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

### 2. Frontend

Open a second PowerShell terminal at the repository root:

```powershell
cd frontend
npm install
npm run dev
```

Vite runs at http://localhost:5173 and proxies `/api` requests to the local backend at port 8000 (see `frontend/vite.config.js`).

### 3. Seed demo data (optional)

With the backend virtual environment activated, from the repository root:

```powershell
python -m backend.seed_demo
```

Seeding is intended to be non-destructive and repeatable. Review generated demo records before presenting them.

### 4. Tests

Backend:

```powershell
python -m pytest backend\tests
```

Frontend production build:

```powershell
cd frontend
npm run build
```

## Project caveats
- Seeded hazard polygons, locations, and synthetic incidents are **illustrative demo data**, not live disaster intelligence.
- Silent-zone scores are experimental and must not be interpreted as verified risk or exposure.
- Whisper model weights may download on first live transcription. Plan for network access and disk space; cached transcripts can be used for offline demo recordings.
- Do not commit real personal data or unconsented bank/identity/voice records.