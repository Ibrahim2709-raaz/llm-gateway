from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ServiceAccountSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateServiceAccountRequest(ServiceAccountSchema):
    name: str = Field(min_length=1, max_length=200)


class CreatedServiceAccountResponse(ServiceAccountSchema):
    id: UUID
    name: str
    role: str
    status: str
    key_prefix: str
    token: str
    created_at: datetime


class ServiceAccountSummary(ServiceAccountSchema):
    id: UUID
    name: str
    role: str
    status: str
    key_prefix: str | None
    created_at: datetime
    last_used_at: datetime | None


class ServiceAccountListResponse(ServiceAccountSchema):
    data: list[ServiceAccountSummary]


class ServiceAccountRevokedResponse(ServiceAccountSchema):
    id: UUID
    status: str
