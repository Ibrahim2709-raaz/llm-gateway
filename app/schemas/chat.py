from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1)


class RoutingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["balanced", "cost", "latency", "quality"] = "balanced"
    allowed_providers: list[str] | None = Field(default=None, min_length=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    disable_cache: bool = False

    @field_validator("allowed_providers")
    @classmethod
    def providers_must_be_unique(cls, providers: list[str] | None) -> list[str] | None:
        if providers is not None and len(providers) != len(set(providers)):
            raise ValueError("allowed_providers must contain unique values")
        return providers


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="auto", min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=512, ge=1, le=32768)
    stream: Literal[False] = False
    routing: RoutingOptions = Field(default_factory=RoutingOptions)


class ResponseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    message: ResponseMessage
    finish_reason: Literal["stop", "length", "content_filter"]


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class RoutingAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    outcome: Literal[
        "success",
        "timeout",
        "rate_limited",
        "unavailable",
        "circuit_open",
        "error",
    ]


class GatewayMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    provider: str
    cost_usd: float = Field(ge=0)
    cached: bool
    routing_strategy: Literal["explicit", "balanced", "cost", "latency", "quality"]
    task_complexity: Literal["simple", "medium", "complex"]
    task_category: Literal[
        "coding", "reasoning", "summarization", "extraction", "general_chat"
    ] = "general_chat"
    classification_confidence: float = Field(default=0, ge=0, le=1)
    classifier_version: str = "legacy-complexity-v1"
    classification_reasons: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = Field(ge=0)
    attempts: list[RoutingAttempt]


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice] = Field(min_length=1)
    usage: TokenUsage
    gateway: GatewayMetadata
