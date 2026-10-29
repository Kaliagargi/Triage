from fastapi import APIRouter

router = APIRouter()

# Placeholder for future health checks or simple routes if needed
@router.get("/ping")
async def ping():
    return {"message": "pong"}
