from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import (
    API_KEY_PREFIX,
    create_client_api_key,
    generate_api_key,
    get_api_key_prefix,
    hash_api_key,
)
from app.auth.dependencies import authenticate_api_key
from app.core.config import Settings
from app.core.errors import GatewayError
from app.db.models import ApiKey, Client
from app.main import create_app


def test_generated_api_key_has_expected_shape_and_digest() -> None:
    generated = generate_api_key("test-pepper")

    assert generated.token.startswith(API_KEY_PREFIX)
    assert len(generated.token) == 51
    assert generated.prefix == generated.token[:16]
    assert generated.digest == hash_api_key(generated.token, "test-pepper")
    assert generated.token not in generated.digest
    assert get_api_key_prefix(generated.token) == generated.prefix
    assert hash_api_key(generated.token, "another-pepper") != generated.digest


@pytest.mark.parametrize("token", ["", "gw_live_short", "not-a-gateway-key"])
def test_invalid_api_key_shape_has_no_prefix(token: str) -> None:
    assert get_api_key_prefix(token) is None


def test_create_client_api_key_stores_only_a_digest() -> None:
    session = Mock(spec=AsyncSession)

    created = create_client_api_key(
        session,
        client_name="  Example Client  ",
        key_name="  production  ",
        pepper="test-pepper",
    )

    stored_client, stored_key = session.add_all.call_args.args[0]
    assert isinstance(stored_client, Client)
    assert isinstance(stored_key, ApiKey)
    assert stored_client.id == created.client_id
    assert stored_client.name == "Example Client"
    assert stored_key.id == created.api_key_id
    assert stored_key.name == "production"
    assert stored_key.key_prefix == created.token[:16]
    assert stored_key.key_hash == hash_api_key(created.token, "test-pepper")
    assert created.token != stored_key.key_hash


@pytest.mark.parametrize(("client_name", "key_name"), [("", "key"), ("client", "  ")])
def test_create_client_api_key_rejects_empty_names(client_name: str, key_name: str) -> None:
    session = Mock(spec=AsyncSession)

    with pytest.raises(ValueError, match="must not be empty"):
        create_client_api_key(
            session,
            client_name=client_name,
            key_name=key_name,
            pepper="test-pepper",
        )


@pytest.mark.anyio
async def test_authenticate_api_key_returns_customer_identity() -> None:
    generated = generate_api_key("test-pepper")
    client_id = uuid4()
    api_key_id = uuid4()
    stored_client = Client(id=client_id, name="Example", status="active")
    stored_key = ApiKey(
        id=api_key_id,
        client_id=client_id,
        name="default",
        key_prefix=generated.prefix,
        key_hash=generated.digest,
    )
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.one_or_none.return_value = (stored_client, stored_key)
    session.execute.return_value = result

    identity = await authenticate_api_key(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=generated.token),
        session=session,
        settings=Settings(_env_file=None, gateway_api_key_pepper="test-pepper"),
    )

    assert identity.client_id == client_id
    assert identity.api_key_id == api_key_id
    assert identity.name == "Example"
    session.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_authenticate_api_key_rejects_unknown_key() -> None:
    generated = generate_api_key("test-pepper")
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.one_or_none.return_value = None
    session.execute.return_value = result

    with pytest.raises(GatewayError) as error:
        await authenticate_api_key(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=generated.token),
            session=session,
            settings=Settings(_env_file=None, gateway_api_key_pepper="test-pepper"),
        )

    assert error.value.status_code == 401
    assert error.value.code == "invalid_api_key"
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.anyio
async def test_authenticate_api_key_rejects_malformed_key_without_database_query() -> None:
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(GatewayError) as error:
        await authenticate_api_key(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid"),
            session=session,
            settings=Settings(_env_file=None),
        )

    assert error.value.code == "invalid_api_key"
    session.execute.assert_not_awaited()


def test_chat_endpoint_requires_a_bearer_key() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert response.json()["error"]["type"] == "authentication_error"
