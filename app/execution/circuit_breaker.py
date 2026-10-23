import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_probe_in_flight: bool = False


class ProviderCircuitBreaker:
    """Process-local circuit breaker shared by all gateway requests."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_seconds <= 0:
            raise ValueError("recovery_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.clock = clock
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def allow_call(self, provider_key: str) -> bool:
        async with self._lock:
            state = self._states.setdefault(provider_key, _CircuitState())
            if state.opened_at is None:
                return True
            if self.clock() - state.opened_at < self.recovery_seconds:
                return False
            if state.half_open_probe_in_flight:
                return False
            state.half_open_probe_in_flight = True
            return True

    async def record_success(self, provider_key: str) -> None:
        async with self._lock:
            self._states.pop(provider_key, None)

    async def record_failure(self, provider_key: str) -> bool:
        """Record a retryable failure and return whether the circuit is open."""
        async with self._lock:
            state = self._states.setdefault(provider_key, _CircuitState())
            state.consecutive_failures += 1
            if (
                state.half_open_probe_in_flight
                or state.consecutive_failures >= self.failure_threshold
            ):
                state.opened_at = self.clock()
                state.half_open_probe_in_flight = False
                return True
            return False
