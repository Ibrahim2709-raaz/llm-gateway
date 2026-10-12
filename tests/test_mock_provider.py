import pytest

from app.providers.mock import MockProvider, estimate_tokens
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def test_token_estimate_is_never_zero() -> None:
    assert estimate_tokens("") == 1


@pytest.mark.anyio
async def test_mock_provider_honors_output_limit() -> None:
    request = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="A message longer than four characters")],
        max_tokens=1,
    )

    result = await MockProvider().complete(request)

    assert result.content == "Mock"
    assert result.completion_tokens == 1
    assert result.finish_reason == "length"
