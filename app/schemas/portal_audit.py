from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditEventResponse(AuditSchema):
    id: UUID
    actor_type: Literal["user", "service_account", "api_key", "system"]
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    request_id: UUID | None
    event_data: dict[str, Any]
    created_at: datetime


class AuditEventListResponse(AuditSchema):
    data: list[AuditEventResponse]
    limit: int = Field(ge=1, le=200)
