from decimal import Decimal
from typing import Any, Literal, cast

import openai
from openai import AsyncOpenAI

from app.providers.base import (
    ModelRoutingProfile,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResult,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.chat import ChatCompletionRequest


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        input_cost_per_million_usd: float,
        output_cost_per_million_usd: float,
        estimated_latency_ms: int = 1500,
        quality_score: float = 0.9,
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
        if request.max_tokens < 16:
            raise ProviderRequestError("OpenAI requires at least 16 output tokens")

        input_messages = [
            {
                "role": "user" if message.role == "tool" else message.role,
                "content": (
                    f"Tool result:\n{message.content}"
                    if message.role == "tool"
                    else message.content
                ),
            }
            for message in request.messages
        ]
        try:
            response = await self.client.responses.create(
                model=self.model,
                input=cast(Any, input_messages),
                max_output_tokens=request.max_tokens,
                temperature=request.temperature,
                store=False,
            )
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError("OpenAI request timed out") from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("OpenAI rate limit exceeded") from exc
        except (openai.APIConnectionError, openai.APIStatusError) as exc:
            raise ProviderUnavailableError("OpenAI request failed") from exc

        if not response.output_text:
            raise ProviderUnavailableError("OpenAI returned no text output")

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        cost_usd = (
            Decimal(input_tokens) * self.input_cost_per_million_usd
            + Decimal(output_tokens) * self.output_cost_per_million_usd
        ) / Decimal(1_000_000)
        incomplete_reason = getattr(response.incomplete_details, "reason", None)
        finish_reason: Literal["stop", "length", "content_filter"] = (
            "length" if incomplete_reason == "max_output_tokens" else "stop"
        )

        return ProviderResult(
            content=response.output_text,
            model=response.model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            finish_reason=finish_reason,
            cost_usd=cost_usd,
        )
