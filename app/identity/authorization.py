from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.identity.roles import (
    AUDIT_ACTOR_TYPES,
    ORGANIZATION_ROLES,
    PLATFORM_ROLES,
    SERVICE_ACCOUNT_ROLES,
)

ActorType = Literal["user", "service_account", "api_key", "system"]

ORGANIZATION_READ = "organization.read"
MEMBERS_MANAGE = "members.manage"
API_KEYS_MANAGE = "api_keys.manage"
BUDGETS_READ = "budgets.read"
BUDGETS_MANAGE = "budgets.manage"
USAGE_READ = "usage.read"
ROUTING_READ = "routing.read"
ROUTING_MANAGE = "routing.manage"
AUDIT_READ = "audit.read"
PLATFORM_MANAGE = "platform.manage"

OWNER_PERMISSIONS = frozenset(
    {
        ORGANIZATION_READ,
        MEMBERS_MANAGE,
        API_KEYS_MANAGE,
        BUDGETS_READ,
        BUDGETS_MANAGE,
        USAGE_READ,
        ROUTING_READ,
        ROUTING_MANAGE,
        AUDIT_READ,
    }
)

ORGANIZATION_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": OWNER_PERMISSIONS,
    "developer": frozenset(
        {
            ORGANIZATION_READ,
            API_KEYS_MANAGE,
            BUDGETS_READ,
            USAGE_READ,
            ROUTING_READ,
            ROUTING_MANAGE,
        }
    ),
    "billing_viewer": frozenset(
        {
            ORGANIZATION_READ,
            BUDGETS_READ,
            USAGE_READ,
        }
    ),
}


class AuthorizationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Principal:
    actor_type: ActorType
    actor_id: UUID | None
    organization_id: UUID | None = None
    organization_role: str | None = None
    platform_role: str | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in AUDIT_ACTOR_TYPES:
            raise ValueError("Unsupported actor type")
        if self.actor_type == "system" and self.actor_id is not None:
            raise ValueError("System actors cannot have an actor ID")
        if self.actor_type != "system" and self.actor_id is None:
            raise ValueError("Non-system actors require an actor ID")
        if self.organization_role is not None and self.organization_role not in ORGANIZATION_ROLES:
            raise ValueError("Unsupported organization role")
        if self.actor_type == "service_account" and self.organization_role not in (
            *SERVICE_ACCOUNT_ROLES,
            None,
        ):
            raise ValueError("Service accounts cannot use that organization role")
        if self.platform_role is not None and self.platform_role not in PLATFORM_ROLES:
            raise ValueError("Unsupported platform role")


def require_organization_permission(
    principal: Principal,
    *,
    organization_id: UUID,
    permission: str,
) -> None:
    if principal.organization_id != organization_id:
        raise AuthorizationError(
            "cross_organization_access",
            "The authenticated principal does not belong to this organization",
        )
    if principal.organization_role is None:
        raise AuthorizationError(
            "organization_role_required",
            "An active organization membership is required",
        )
    allowed = ORGANIZATION_ROLE_PERMISSIONS[principal.organization_role]
    if permission not in allowed:
        raise AuthorizationError(
            "permission_denied",
            f"The {principal.organization_role} role cannot perform this action",
        )


def require_platform_permission(principal: Principal, *, permission: str) -> None:
    if principal.platform_role != "platform_operator" or permission != PLATFORM_MANAGE:
        raise AuthorizationError(
            "platform_permission_denied",
            "Platform operator permission is required",
        )
