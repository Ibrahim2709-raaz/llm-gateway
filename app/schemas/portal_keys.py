from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateApiKeyRequest(ApiKeySchema):
    name: str = Field(min_length=1, max_length=200)


class CreatedApiKeyResponse(ApiKeySchema):
    id: UUID
    name: str
    key_prefix: str
    token: str
    created_at: datetime


class ApiKeySummary(ApiKeySchema):
    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyListResponse(ApiKeySchema):
    data: list[ApiKeySummary]


class ApiKeyRevokedResponse(ApiKeySchema):
    id: UUID
    revoked_at: datetime
