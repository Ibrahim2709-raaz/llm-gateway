import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Client

API_KEY_PREFIX = "gw_live_"
API_KEY_RANDOM_BYTES = 32
API_KEY_VISIBLE_PREFIX_LENGTH = 16
API_KEY_PATTERN = re.compile(r"^gw_live_[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    token: str
    prefix: str
    digest: str


@dataclass(frozen=True, slots=True)
class CreatedClientApiKey:
    client_id: UUID
    api_key_id: UUID
    token: str


def hash_api_key(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def get_api_key_prefix(token: str) -> str | None:
    if API_KEY_PATTERN.fullmatch(token) is None:
        return None
    return token[:API_KEY_VISIBLE_PREFIX_LENGTH]


def generate_api_key(pepper: str) -> GeneratedApiKey:
    token = f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_RANDOM_BYTES)}"
    prefix = token[:API_KEY_VISIBLE_PREFIX_LENGTH]
    return GeneratedApiKey(token=token, prefix=prefix, digest=hash_api_key(token, pepper))


def create_client_api_key(
    session: AsyncSession,
    *,
    client_name: str,
    key_name: str,
    pepper: str,
) -> CreatedClientApiKey:
    normalized_client_name = client_name.strip()
    normalized_key_name = key_name.strip()
    if not normalized_client_name or not normalized_key_name:
        raise ValueError("Client and key names must not be empty")

    generated = generate_api_key(pepper)
    client_id = uuid4()
    api_key_id = uuid4()
    session.add_all(
        [
            Client(id=client_id, name=normalized_client_name, status="active"),
            ApiKey(
                id=api_key_id,
                client_id=client_id,
                name=normalized_key_name,
                key_prefix=generated.prefix,
                key_hash=generated.digest,
            ),
        ]
    )
    return CreatedClientApiKey(client_id=client_id, api_key_id=api_key_id, token=generated.token)
