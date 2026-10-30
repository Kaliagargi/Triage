import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "TRACE-India Core Engine"
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://postgres:postgres@localhost:5432/trace_india"
    )
    ENVIRONMENT: str = "production"

    class Config:
        env_file = ".env"

settings = Settings()