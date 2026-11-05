from fastapi import APIRouter, Depends

from lib.response import StandardResponse

router = APIRouter()

@router.get("/")
async def root():
    return StandardResponse("ok", data={"version":"0.0.1"}).success()