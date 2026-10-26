from decimal import Decimal
from typing import Any, Literal, cast

import anthropic
from anthropic import AsyncAnthropic

from app.providers.base import (
    ModelRoutingProfile,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.chat import ChatCompletionRequest


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        client: AsyncAnthropic,
        *,
        model: str,
        input_cost_per_million_usd: float,
        output_cost_per_million_usd: float,
        estimated_latency_ms: int = 1800,
        quality_score: float = 0.92,
    ) -> None:
        self.client = client
        self.model = model
        self.input_cost_per_million_usd = Decimal(str(input_cost_per_million_usd))
        self.output_cost_per_million_usd = Decimal(str(output_cost_per_million_usd))
        self.routing_profile = ModelRoutingProfile(
            input_cost_per_million_usd=self.input_cost_per_million_usd,
            output_cost_per_million_usd=self.output_cost_per_million_usd,
            estimated_latency_ms=estimated_latency_ms,
            quality_score=quality_score,
        )

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult:
        system = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        messages = [
            {
                "role": "user" if message.role == "tool" else message.role,
                "content": (
                    f"Tool result:\n{message.content}"
                    if message.role == "tool"
                    else message.content
                ),
            }
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise ProviderRequestError("Anthropic requires at least one user message")
        if messages[-1]["role"] == "assistant":
            raise ProviderRequestError("Anthropic does not support assistant prefills")

        request_options: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if system:
            request_options["system"] = system

        try:
            response = await self.client.messages.create(**cast(Any, request_options))
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError("Anthropic request timed out") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitError("Anthropic rate limit exceeded") from exc
        except anthropic.BadRequestError as exc:
            raise ProviderRequestError("Anthropic rejected the request") from exc
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            raise ProviderUnavailableError("Anthropic request failed") from exc

        text_parts = [
            cast(str, block.text)
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        content = "".join(text_parts)
        finish_reason = self._finish_reason(response.stop_reason)
        if not content and finish_reason != "content_filter":
            raise ProviderUnavailableError("Anthropic returned no text output")

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_usd = (
            Decimal(input_tokens) * self.input_cost_per_million_usd
            + Decimal(output_tokens) * self.output_cost_per_million_usd
        ) / Decimal(1_000_000)

        return ProviderResult(
            content=content,
            model=response.model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            finish_reason=finish_reason,
            cost_usd=cost_usd,
        )

    @staticmethod
    def _finish_reason(
        stop_reason: str | None,
    ) -> Literal["stop", "length", "content_filter"]:
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            return "length"
        if stop_reason == "refusal":
            return "content_filter"
        return "stop"
