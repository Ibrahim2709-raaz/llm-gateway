from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: Literal["stop", "length", "content_filter"]
    cost_usd: Decimal = Decimal("0")


TaskComplexity = Literal["simple", "medium", "complex"]


@dataclass(frozen=True, slots=True)
class ModelRoutingProfile:
    input_cost_per_million_usd: Decimal
    output_cost_per_million_usd: Decimal
    estimated_latency_ms: int
    quality_score: float
    max_complexity: TaskComplexity = "complex"
    auto_selectable: bool = True


class ProviderError(Exception):
    """Base class for sanitized provider failures."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond within its configured timeout."""


class ProviderRateLimitError(ProviderError):
    """The upstream provider rejected the request due to its rate limit."""


class ProviderRequestError(ProviderError):
    """The request is unsupported by the selected provider."""


class ProviderUnavailableError(ProviderError):
    """The provider could not serve the request."""


class ChatProvider(Protocol):
    name: str
    model: str
    routing_profile: ModelRoutingProfile

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult: ...
