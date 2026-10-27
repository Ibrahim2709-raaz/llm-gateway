from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoutingPolicyActivation, RoutingPolicyVersion
from app.routing.policy_activation import PolicyActivationError, change_active_policy

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def make_policy(version: str, status: str) -> RoutingPolicyVersion:
    return RoutingPolicyVersion(
        id=uuid4(),
        version=version,
        status=status,
        classifier_version="classifier-v1",
        score_version="scores-v1",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="static_profile",
    )


def make_session(*policies: RoutingPolicyVersion) -> AsyncMock:
    result = Mock()
    result.scalars.return_value.all.return_value = list(policies)
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result
    return session


@pytest.mark.anyio
async def test_activate_draft_retires_current_and_writes_audit_event() -> None:
    current = make_policy("baseline-v1", "active")
    target = make_policy("candidate-v1", "draft")
    session = make_session(current, target)

    activation = await change_active_policy(
        session,
        version="candidate-v1",
        action="activate",
        actor="release-bot",
        reason="shadow gate passed",
        now=NOW,
    )

    assert current.status == "retired"
    assert target.status == "active"
    assert target.activated_at == NOW
    assert activation.policy_version == "candidate-v1"
    assert activation.previous_policy_version == "baseline-v1"
    event = session.add.call_args.args[0]
    assert isinstance(event, RoutingPolicyActivation)
    assert event.policy_id == target.id
    assert event.previous_policy_id == current.id
    assert event.action == "activate"
    assert event.actor == "release-bot"
    assert event.reason == "shadow gate passed"
    assert session.flush.await_count == 2


@pytest.mark.anyio
async def test_rollback_reactivates_retired_policy_and_audits_transition() -> None:
    current = make_policy("candidate-v1", "active")
    target = make_policy("baseline-v1", "retired")
    session = make_session(current, target)

    activation = await change_active_policy(
        session,
        version="baseline-v1",
        action="rollback",
        actor="operator@example.com",
        now=NOW,
    )

    assert current.status == "retired"
    assert target.status == "active"
    assert activation.action == "rollback"
    assert activation.previous_policy_version == "candidate-v1"
    event = session.add.call_args.args[0]
    assert event.action == "rollback"
    assert event.actor == "operator@example.com"


@pytest.mark.parametrize(
    ("action", "target_status", "code"),
    [
        ("activate", "retired", "invalid_policy_status"),
        ("rollback", "draft", "invalid_policy_status"),
    ],
)
@pytest.mark.anyio
async def test_transition_rejects_wrong_source_status(
    action: str, target_status: str, code: str
) -> None:
    current = make_policy("current-v1", "active")
    target = make_policy("target-v1", target_status)
    session = make_session(current, target)

    with pytest.raises(PolicyActivationError) as error:
        await change_active_policy(
            session,
            version="target-v1",
            action=action,  # type: ignore[arg-type]
            actor="operator",
            now=NOW,
        )

    assert error.value.code == code
    assert current.status == "active"
    assert target.status == target_status
    session.add.assert_not_called()


@pytest.mark.anyio
async def test_transition_rejects_missing_and_already_active_policy() -> None:
    current = make_policy("current-v1", "active")

    with pytest.raises(PolicyActivationError) as missing:
        await change_active_policy(
            make_session(current),
            version="missing-v1",
            action="activate",
            actor="operator",
            now=NOW,
        )
    assert missing.value.code == "policy_not_found"

    with pytest.raises(PolicyActivationError) as active:
        await change_active_policy(
            make_session(current),
            version="current-v1",
            action="activate",
            actor="operator",
            now=NOW,
        )
    assert active.value.code == "already_active"


@pytest.mark.anyio
async def test_activation_requires_a_named_actor() -> None:
    with pytest.raises(PolicyActivationError) as error:
        await change_active_policy(
            make_session(),
            version="candidate-v1",
            action="activate",
            actor="  ",
            now=NOW,
        )

    assert error.value.code == "invalid_actor"
