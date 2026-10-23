from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.observability.metrics import METRICS

router = APIRouter(tags=["Observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    return Response(
        content=METRICS.render(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
