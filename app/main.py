from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import db_manager
from app.main import app as core_api_router # assuming core endpoints

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    await db_manager.connect()
    yield
    # Shutdown actions
    await db_manager.disconnect()

app = FastAPI(title="TRACE-India Unified Platform", version="1.0.0", lifespan=lifespan)