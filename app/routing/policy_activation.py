from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoutingPolicyActivation, RoutingPolicyVersion

ActivationAction = Literal["activate", "rollback"]


class PolicyActivationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PolicyActivationResult:
    policy_id: UUID
    policy_version: str
    previous_policy_version: str | None
    action: ActivationAction
    activated_at: datetime


async def change_active_policy(
    session: AsyncSession,
    *,
    version: str,
    action: ActivationAction,
    actor: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> PolicyActivationResult:
    normalized_version = version.strip()
    normalized_actor = actor.strip()
    normalized_reason = reason.strip() if reason else None
    if not normalized_version:
        raise PolicyActivationError("invalid_version", "Policy version must not be empty")
    if not normalized_actor:
        raise PolicyActivationError("invalid_actor", "Activation actor must not be empty")
    if len(normalized_actor) > 200:
        raise PolicyActivationError(
            "invalid_actor", "Activation actor must be 200 characters or fewer"
        )
    if normalized_reason is not None and len(normalized_reason) > 500:
        raise PolicyActivationError(
            "invalid_reason", "Activation reason must be 500 characters or fewer"
        )

    policies = (
        (
            await session.execute(
                select(RoutingPolicyVersion)
                .where(
                    or_(
                        RoutingPolicyVersion.version == normalized_version,
                        RoutingPolicyVersion.status == "active",
                    )
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    target = next((policy for policy in policies if policy.version == normalized_version), None)
    if target is None:
        raise PolicyActivationError(
            "policy_not_found", f"Routing policy '{normalized_version}' does not exist"
        )

    active_policies = [policy for policy in policies if policy.status == "active"]
    if len(active_policies) > 1:
        raise PolicyActivationError(
            "multiple_active_policies", "More than one active routing policy was found"
        )
    current = active_policies[0] if active_policies else None
    if current is target:
        raise PolicyActivationError(
            "already_active", f"Routing policy '{normalized_version}' is already active"
        )

    expected_status = "draft" if action == "activate" else "retired"
    if target.status != expected_status:
        raise PolicyActivationError(
            "invalid_policy_status",
            f"Policy '{normalized_version}' must be {expected_status} to {action}",
        )
    if action == "rollback" and current is None:
        raise PolicyActivationError(
            "no_active_policy", "Rollback requires a currently active routing policy"
        )

    activated_at = now or datetime.now(UTC)
    previous_version = current.version if current is not None else None
    if current is not None:
        current.status = "retired"
        await session.flush()

    target.status = "active"
    target.activated_at = activated_at
    session.add(
        RoutingPolicyActivation(
            policy_id=target.id,
            previous_policy_id=current.id if current is not None else None,
            action=action,
            actor=normalized_actor,
            reason=normalized_reason,
            activated_at=activated_at,
        )
    )
    await session.flush()
    return PolicyActivationResult(
        policy_id=target.id,
        policy_version=target.version,
        previous_policy_version=previous_version,
        action=action,
        activated_at=activated_at,
    )
