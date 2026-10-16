from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import get_api_key_prefix, hash_api_key
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError
from app.db.models import ApiKey, Client
from app.db.session import get_database_session

bearer_auth = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    client_id: UUID
    api_key_id: UUID
    name: str


def invalid_api_key_error() -> GatewayError:
    return GatewayError(
        "The supplied gateway API key is invalid.",
        code="invalid_api_key",
        error_type="authentication_error",
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedClient:
    if credentials is None:
        raise invalid_api_key_error()

    token = credentials.credentials
    prefix = get_api_key_prefix(token)
    if prefix is None:
        raise invalid_api_key_error()

    digest = hash_api_key(token, settings.gateway_api_key_pepper.get_secret_value())
    statement = (
        select(Client, ApiKey)
        .join(ApiKey, ApiKey.client_id == Client.id)
        .where(
            ApiKey.key_prefix == prefix,
            ApiKey.key_hash == digest,
            ApiKey.revoked_at.is_(None),
            Client.status == "active",
        )
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise invalid_api_key_error()

    client, api_key = row
    return AuthenticatedClient(client_id=client.id, api_key_id=api_key.id, name=client.name)
