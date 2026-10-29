from uuid import uuid4

import pytest

from app.identity.audit import (
    AuditEventValidationError,
    build_audit_event,
    validate_event_data,
)
from app.identity.authorization import Principal


def test_build_audit_event_uses_safe_principal_and_resource_metadata() -> None:
    principal = Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=uuid4(),
        organization_role="owner",
    )

    event = build_audit_event(
        principal=principal,
        organization_id=principal.organization_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=str(uuid4()),
        event_data={"key_prefix": "gw_live_abc", "role": "developer"},
    )

    assert event.actor_id == principal.actor_id
    assert event.organization_id == principal.organization_id
    assert event.event_data["key_prefix"] == "gw_live_abc"


@pytest.mark.parametrize(
    "event_data",
    [
        {"prompt": "private customer text"},
        {"nested": {"response": "private model output"}},
        {"items": [{"authorization": "Bearer secret"}]},
        {"token": "one-time-value"},
    ],
)
def test_audit_event_data_rejects_sensitive_fields(event_data: dict[str, object]) -> None:
    with pytest.raises(AuditEventValidationError, match="sensitive field"):
        validate_event_data(event_data)


def test_audit_event_data_allows_identifiers_and_safe_reason_codes() -> None:
    validate_event_data(
        {
            "api_key_id": str(uuid4()),
            "policy_version": "balanced-v2",
            "reason_codes": ["quality_floor_met", "within_budget"],
        }
    )


def test_audit_event_data_has_a_bounded_size() -> None:
    with pytest.raises(AuditEventValidationError, match="16 KiB"):
        validate_event_data({"details": "x" * 17_000})
