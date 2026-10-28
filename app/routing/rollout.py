from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID


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
