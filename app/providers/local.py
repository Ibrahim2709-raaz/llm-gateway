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
    ProviderToolCall,
    ProviderUnavailableError,
)
from app.schemas.chat import ChatCompletionRequest, NamedToolChoice


class LocalOpenAIProvider:
    name = "local"

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        input_cost_per_million_usd: float = 0,
        output_cost_per_million_usd: float = 0,
        estimated_latency_ms: int = 2500,
        quality_score: float = 0.7,
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
        ]
        request_options: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.response_format.type != "text":
            request_options["response_format"] = request.response_format.model_dump(
                by_alias=True, exclude_none=True
            )
        if request.tools is not None:
            request_options["tools"] = [
                tool.model_dump(by_alias=True, exclude_none=True) for tool in request.tools
            ]
            request_options["tool_choice"] = (
                request.tool_choice.model_dump(by_alias=True, exclude_none=True)
                if isinstance(request.tool_choice, NamedToolChoice)
                else request.tool_choice
            )
        try:
            response = await self.client.chat.completions.create(**cast(Any, request_options))
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError("Local provider request timed out") from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("Local provider rate limit exceeded") from exc
        except openai.BadRequestError as exc:
            raise ProviderRequestError("Local provider rejected the request") from exc
        except (openai.APIConnectionError, openai.APIStatusError) as exc:
            raise ProviderUnavailableError("Local provider request failed") from exc

        if not response.choices:
            raise ProviderUnavailableError("Local provider returned no choices")
        choice = response.choices[0]
        tool_calls = tuple(
            ProviderToolCall(
                id=str(tool_call.id),
                name=str(tool_call.function.name),
                arguments=str(tool_call.function.arguments),
            )
            for tool_call in (getattr(choice.message, "tool_calls", None) or ())
        )
        if not choice.message.content and not tool_calls:
            raise ProviderUnavailableError("Local provider returned no text output")

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage is not None else 0
        completion_tokens = usage.completion_tokens if usage is not None else 0
        cost_usd = (
            Decimal(prompt_tokens) * self.input_cost_per_million_usd
            + Decimal(completion_tokens) * self.output_cost_per_million_usd
        ) / Decimal(1_000_000)
        finish_reason: Literal["stop", "length", "content_filter", "tool_calls"]
        if choice.finish_reason == "length":
            finish_reason = "length"
        elif choice.finish_reason == "content_filter":
            finish_reason = "content_filter"
        elif tool_calls:
            finish_reason = "tool_calls"
        else:
            finish_reason = "stop"

        return ProviderResult(
            content=choice.message.content,
            model=response.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
        )
