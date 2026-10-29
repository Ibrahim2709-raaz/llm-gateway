from uuid import UUID, uuid4

import pytest

from app.identity.authorization import (
    API_KEYS_MANAGE,
    BUDGETS_MANAGE,
    BUDGETS_READ,
    MEMBERS_MANAGE,
    PLATFORM_MANAGE,
    AuthorizationError,
    Principal,
    require_organization_permission,
    require_platform_permission,
)


def principal(role: str, *, organization_id: UUID | None = None) -> Principal:
    resolved_organization_id = organization_id or uuid4()
    return Principal(
        actor_type="user",
        actor_id=uuid4(),
        organization_id=resolved_organization_id,
        organization_role=role,
    )


def test_owner_can_manage_members_keys_and_budgets() -> None:
    organization_id = uuid4()
    owner = principal("owner", organization_id=organization_id)

    for permission in (MEMBERS_MANAGE, API_KEYS_MANAGE, BUDGETS_MANAGE):
        require_organization_permission(
            owner,
            organization_id=organization_id,
            permission=permission,
        )


def test_billing_viewer_can_read_but_cannot_change_budget() -> None:
    organization_id = uuid4()
    billing_viewer = principal("billing_viewer", organization_id=organization_id)

    require_organization_permission(
        billing_viewer,
        organization_id=organization_id,
        permission=BUDGETS_READ,
    )
    with pytest.raises(AuthorizationError, match="cannot perform") as exc_info:
        require_organization_permission(
            billing_viewer,
            organization_id=organization_id,
            permission=BUDGETS_MANAGE,
        )

    assert exc_info.value.code == "permission_denied"


def test_cross_organization_access_is_denied_before_role_check() -> None:
    owner = principal("owner")

    with pytest.raises(AuthorizationError) as exc_info:
        require_organization_permission(
            owner,
            organization_id=uuid4(),
            permission=MEMBERS_MANAGE,
        )

    assert exc_info.value.code == "cross_organization_access"


def test_platform_operator_does_not_implicitly_bypass_tenant_scope() -> None:
    operator = Principal(
        actor_type="user",
        actor_id=uuid4(),
        platform_role="platform_operator",
    )

    require_platform_permission(operator, permission=PLATFORM_MANAGE)
    with pytest.raises(AuthorizationError) as exc_info:
        require_organization_permission(
            operator,
            organization_id=uuid4(),
            permission=BUDGETS_READ,
        )

    assert exc_info.value.code == "cross_organization_access"


def test_service_account_cannot_assume_owner_role() -> None:
    with pytest.raises(ValueError, match="Service accounts"):
        Principal(
            actor_type="service_account",
            actor_id=uuid4(),
            organization_id=uuid4(),
            organization_role="owner",
        )
