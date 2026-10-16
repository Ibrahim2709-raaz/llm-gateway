from decimal import Decimal
from math import ceil
from typing import Literal

from app.providers.base import ModelRoutingProfile, ProviderResult
from app.schemas.chat import ChatCompletionRequest


def estimate_tokens(text: str) -> int:
    return max(1, ceil(len(text) / 4))


class MockProvider:
    name = "mock"
    model = "mock-1"
    routing_profile = ModelRoutingProfile(
        input_cost_per_million_usd=Decimal("0"),
        output_cost_per_million_usd=Decimal("0"),
        estimated_latency_ms=5,
        quality_score=0.1,
        auto_selectable=False,
    )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        latest_message = request.messages[-1]
        content = f"Mock response: {latest_message.content}"
        finish_reason: Literal["stop", "length", "content_filter"] = "stop"
        character_limit = request.max_tokens * 4
        if len(content) > character_limit:
            content = content[:character_limit]
            finish_reason = "length"

        prompt = "\n".join(f"{message.role}: {message.content}" for message in request.messages)
        return ProviderResult(
            content=content,
            model=self.model,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(content),
            finish_reason=finish_reason,
        )
