import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.identity.authorization import Principal

MAX_AUDIT_EVENT_DATA_BYTES = 16_384
FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "password_hash",
        "prompt",
        "raw_prompt",
        "raw_response",
        "response",
        "secret",
        "token",
        "token_hash",
    }
)


class AuditEventValidationError(ValueError):
    pass


def _validate_event_data(value: Any, *, path: str = "event_data") -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in FORBIDDEN_AUDIT_KEYS:
                raise AuditEventValidationError(
                    f"Audit event data cannot include sensitive field '{path}.{key}'"
                )
            _validate_event_data(nested_value, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested_value in enumerate(value):
            _validate_event_data(nested_value, path=f"{path}[{index}]")


def validate_event_data(event_data: dict[str, Any]) -> None:
    _validate_event_data(event_data)
    try:
        encoded = json.dumps(event_data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuditEventValidationError("Audit event data must be JSON serializable") from exc
    if len(encoded) > MAX_AUDIT_EVENT_DATA_BYTES:
        raise AuditEventValidationError("Audit event data exceeds the 16 KiB limit")


def build_audit_event(
    *,
    principal: Principal,
    action: str,
    resource_type: str,
    organization_id: UUID | None = None,
    resource_id: str | None = None,
    request_id: UUID | None = None,
    event_data: dict[str, Any] | None = None,
) -> AuditEvent:
    normalized_action = action.strip()
    normalized_resource_type = resource_type.strip()
    if not normalized_action or len(normalized_action) > 100:
        raise AuditEventValidationError("Audit action must contain 1 to 100 characters")
    if not normalized_resource_type or len(normalized_resource_type) > 100:
        raise AuditEventValidationError("Resource type must contain 1 to 100 characters")
    if resource_id is not None and len(resource_id) > 200:
        raise AuditEventValidationError("Resource ID cannot exceed 200 characters")
    data = dict(event_data or {})
    validate_event_data(data)

    return AuditEvent(
        organization_id=organization_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        action=normalized_action,
        resource_type=normalized_resource_type,
        resource_id=resource_id,
        request_id=request_id,
        event_data=data,
    )


async def append_audit_event(
    session: AsyncSession,
    *,
    principal: Principal,
    action: str,
    resource_type: str,
    organization_id: UUID | None = None,
    resource_id: str | None = None,
    request_id: UUID | None = None,
    event_data: dict[str, Any] | None = None,
) -> AuditEvent:
    event = build_audit_event(
        principal=principal,
        action=action,
        resource_type=resource_type,
        organization_id=organization_id,
        resource_id=resource_id,
        request_id=request_id,
        event_data=event_data,
    )
    session.add(event)
    await session.flush()
    return event
