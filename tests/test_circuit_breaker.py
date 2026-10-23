import pytest

from app.execution.circuit_breaker import ProviderCircuitBreaker


@pytest.mark.anyio
async def test_circuit_opens_and_allows_one_recovery_probe() -> None:
    now = [100.0]
    breaker = ProviderCircuitBreaker(
        failure_threshold=2,
        recovery_seconds=30,
        clock=lambda: now[0],
    )

    assert await breaker.allow_call("openai:model") is True
    assert await breaker.record_failure("openai:model") is False
    assert await breaker.allow_call("openai:model") is True
    assert await breaker.record_failure("openai:model") is True
    assert await breaker.allow_call("openai:model") is False

    now[0] += 30
    assert await breaker.allow_call("openai:model") is True
    assert await breaker.allow_call("openai:model") is False


@pytest.mark.anyio
async def test_successful_recovery_probe_closes_circuit() -> None:
    now = [100.0]
    breaker = ProviderCircuitBreaker(
        failure_threshold=1,
        recovery_seconds=10,
        clock=lambda: now[0],
    )

    assert await breaker.record_failure("openai:model") is True
    now[0] += 10
    assert await breaker.allow_call("openai:model") is True
    await breaker.record_success("openai:model")

    assert await breaker.allow_call("openai:model") is True
