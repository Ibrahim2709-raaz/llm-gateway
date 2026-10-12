from dataclasses import dataclass
from typing import Literal, Protocol

from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: Literal["stop", "length", "content_filter"]


class ChatProvider(Protocol):
    name: str
    model: str

    async def complete(self, request: ChatCompletionRequest) -> ProviderResult: ...
