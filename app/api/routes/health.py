from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/health", tags=["Health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"] = "ready"
    checks: dict[str, Literal["ok", "unavailable"]]


@router.get("/live", response_model=LivenessResponse, operation_id="getLiveness")
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse, operation_id="getReadiness")
async def readiness() -> ReadinessResponse:
    # PostgreSQL and Redis checks are added in Step 4.
    return ReadinessResponse(checks={})

