from uuid import UUID, uuid4

import pytest

from app.routing.rollout import IntelligentRoutingRollout


def test_allowlisted_customer_is_included_at_zero_percent() -> None:
    client_id = uuid4()

    rollout = IntelligentRoutingRollout(
        percentage=0,
        customer_allowlist=frozenset({client_id}),
    )

    assert rollout.includes(client_id)
    assert not rollout.includes(uuid4())


def test_percentage_selection_is_stable_and_has_expected_boundaries() -> None:
    client_id = UUID("d8a3f20b-53c6-44b7-881c-2ad452e0304e")

    first = IntelligentRoutingRollout(percentage=25).includes(client_id)
    second = IntelligentRoutingRollout(percentage=25).includes(client_id)

    assert first is second
    assert not IntelligentRoutingRollout(percentage=0).includes(client_id)
    assert IntelligentRoutingRollout(percentage=100).includes(client_id)


@pytest.mark.parametrize("percentage", [-1, 101])
def test_invalid_percentage_is_rejected(percentage: int) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        IntelligentRoutingRollout(percentage=percentage)
