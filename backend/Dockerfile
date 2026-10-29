FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 -r requirements.txt
COPY backend/ ./backend/
ENV DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/trace_india
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
