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
from app.schemas.chat import (
    ChatCompletionRequest,
    JsonObjectResponseFormat,
    JsonSchemaResponseFormat,
    NamedToolChoice,
)


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
            capabilities=frozenset({"text_generation", "structured_output", "tool_use"}),
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
        request_options: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": request.max_tokens,
            "temperature": request.temperature,
            "store": False,
        }
        if isinstance(request.response_format, JsonObjectResponseFormat):
            request_options["text"] = {"format": {"type": "json_object"}}
        elif isinstance(request.response_format, JsonSchemaResponseFormat):
            definition = request.response_format.json_schema
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "name": definition.name,
                "schema": definition.schema_,
                "strict": definition.strict,
            }
            if definition.description is not None:
                response_format["description"] = definition.description
            request_options["text"] = {"format": response_format}
        if request.tools is not None:
            request_options["tools"] = [
                {
                    "type": "function",
                    "name": tool.function.name,
                    "description": tool.function.description,
                    "parameters": tool.function.parameters,
                    "strict": tool.function.strict,
                }
                for tool in request.tools
            ]
            if isinstance(request.tool_choice, NamedToolChoice):
                request_options["tool_choice"] = {
                    "type": "function",
                    "name": request.tool_choice.function.name,
                }
            else:
                request_options["tool_choice"] = request.tool_choice
        try:
            response = await self.client.responses.create(**cast(Any, request_options))
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError("OpenAI request timed out") from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("OpenAI rate limit exceeded") from exc
        except (openai.APIConnectionError, openai.APIStatusError) as exc:
            raise ProviderUnavailableError("OpenAI request failed") from exc

        tool_calls = tuple(
            ProviderToolCall(
                id=str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                name=str(getattr(item, "name", "")),
                arguments=str(getattr(item, "arguments", "{}")),
            )
            for item in getattr(response, "output", ())
            if getattr(item, "type", None) == "function_call"
        )
        if not response.output_text and not tool_calls:
            raise ProviderUnavailableError("OpenAI returned no text output")

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        cost_usd = (
            Decimal(input_tokens) * self.input_cost_per_million_usd
            + Decimal(output_tokens) * self.output_cost_per_million_usd
        ) / Decimal(1_000_000)
        incomplete_reason = getattr(response.incomplete_details, "reason", None)
        finish_reason: Literal["stop", "length", "content_filter", "tool_calls"]
        if incomplete_reason == "max_output_tokens":
            finish_reason = "length"
        elif tool_calls:
            finish_reason = "tool_calls"
        else:
            finish_reason = "stop"

        return ProviderResult(
            content=response.output_text or None,
            model=response.model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            finish_reason=finish_reason,
            cost_usd=cost_usd,
            tool_calls=tool_calls,
        )
