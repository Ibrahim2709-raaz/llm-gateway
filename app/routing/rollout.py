from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

RoutingMode = Literal["baseline", "shadow", "live"]


@dataclass(frozen=True, slots=True)
class IntelligentRoutingRollout:
    """Deterministically selects customers for a live routing rollout."""

    percentage: int = 100
    customer_allowlist: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if not 0 <= self.percentage <= 100:
            raise ValueError("Rollout percentage must be between 0 and 100")

    def includes(self, client_id: UUID) -> bool:
        if client_id in self.customer_allowlist:
            return True
        if self.percentage == 0:
            return False
        if self.percentage == 100:
            return True
        bucket = int.from_bytes(sha256(client_id.bytes).digest()[:8], "big") % 10_000
        return bucket < self.percentage * 100


@dataclass(frozen=True, slots=True)
class RoutingRuntimeConfiguration:
    mode: RoutingMode
    rollout: IntelligentRoutingRollout
    required_policy_version: str | None = None
    required_score_version: str | None = None
    source: str = "environment"
    version: int | None = None

    def live_selected(self, client_id: UUID) -> bool:
        return self.mode == "live" and self.rollout.includes(client_id)


class RoutingControlResolver(Protocol):
    async def load(self) -> RoutingRuntimeConfiguration: ...
