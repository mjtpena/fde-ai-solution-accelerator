from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]


router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readyz() -> HealthResponse:
    return HealthResponse(status="ready")
