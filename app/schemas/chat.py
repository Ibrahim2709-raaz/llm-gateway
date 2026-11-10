from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FunctionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=4096)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    strict: bool = False


class FunctionTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    function: FunctionDefinition


class NamedFunctionToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class NamedToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    function: NamedFunctionToolChoice


class TextResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"


class JsonObjectResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["json_object"]


class JsonSchemaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=4096)
    schema_: dict[str, Any] = Field(alias="schema")
    strict: bool = True


class JsonSchemaResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["json_schema"]
    json_schema: JsonSchemaDefinition


ResponseFormat = TextResponseFormat | JsonObjectResponseFormat | JsonSchemaResponseFormat
ToolChoice = Literal["none", "auto", "required"] | NamedToolChoice


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
    response_format: ResponseFormat = Field(default_factory=TextResponseFormat)
    tools: list[FunctionTool] | None = Field(default=None, min_length=1, max_length=128)
    tool_choice: ToolChoice = "auto"
    routing: RoutingOptions = Field(default_factory=RoutingOptions)

    @model_validator(mode="after")
    def validate_tool_choice(self) -> "ChatCompletionRequest":
        if self.tools is None:
            if isinstance(self.tool_choice, NamedToolChoice) or self.tool_choice == "required":
                raise ValueError("tool_choice requires at least one tool")
            return self
        names = [tool.function.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        if isinstance(self.tool_choice, NamedToolChoice):
            if self.tool_choice.function.name not in names:
                raise ValueError("tool_choice must name a declared tool")
        return self


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ResponseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def require_content_or_tool_calls(self) -> "ResponseMessage":
        if self.content is None and not self.tool_calls:
            raise ValueError("assistant response requires content or tool calls")
        return self


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    message: ResponseMessage
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"]


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
    task_category: Literal["coding", "reasoning", "summarization", "extraction", "general_chat"] = (
        "general_chat"
    )
    classification_confidence: float = Field(default=0, ge=0, le=1)
    classifier_version: str = "legacy-complexity-v1"
    classification_reasons: list[str] = Field(default_factory=list)
    routing_policy_version: str = "baseline-v1"
    score_version: str = "static-model-profiles-v1"
    routing_reasons: list[str] = Field(default_factory=list)
    evaluated_quality: float | None = Field(default=None, ge=0, le=1)
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
