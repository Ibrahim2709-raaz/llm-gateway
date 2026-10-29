import hashlib
import secrets
from dataclasses import dataclass

TOKEN_ENTROPY_BYTES = 32


@dataclass(frozen=True, slots=True)
class IssuedOpaqueToken:
    value: str
    prefix: str
    hash: str


def hash_opaque_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_opaque_token(*, namespace: str) -> IssuedOpaqueToken:
    normalized_namespace = namespace.strip().lower()
    if not normalized_namespace or not normalized_namespace.replace("_", "").isalnum():
        raise ValueError("Token namespace must contain letters, numbers, or underscores")
    random_value = secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)
    value = f"{normalized_namespace}_{random_value}"
    return IssuedOpaqueToken(
        value=value,
        prefix=value[:24],
        hash=hash_opaque_token(value),
    )
