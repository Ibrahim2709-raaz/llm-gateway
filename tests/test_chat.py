from uuid import UUID

import pytest
from fastapi.testclient import TestClient


def test_create_chat_completion_with_mock_provider(client: TestClient) -> None:
    request_id = "0a8b43e4-47f0-4eb7-b5a1-8f56d1dc40d5"
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Request-ID": request_id},
        json={
            "model": "auto",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Why use a gateway?"},
            ],
            "temperature": 0.2,
            "max_tokens": 100,
            "routing": {"strategy": "cost", "allowed_providers": ["mock"]},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("chatcmpl_")
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-1"
    assert body["choices"] == [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Mock response: Why use a gateway?",
            },
            "finish_reason": "stop",
        }
    ]
    assert body["usage"]["total_tokens"] == (
        body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )
    assert body["gateway"] == {
        "request_id": request_id,
        "provider": "mock",
        "cost_usd": 0.0,
        "cached": False,
        "routing_strategy": "cost",
        "task_complexity": "simple",
        "estimated_cost_usd": 0.0,
        "attempts": [{"provider": "mock", "model": "mock-1", "outcome": "success"}],
    }
    assert response.headers["x-request-id"] == request_id
    assert response.headers["x-ratelimit-limit"] == "60"
    assert response.headers["x-ratelimit-remaining"] == "59"
    assert response.headers["x-ratelimit-reset"] == "1790707260"
    UUID(body["gateway"]["request_id"])


def test_explicit_mock_model_marks_routing_as_explicit(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "mock-1", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["gateway"]["routing_strategy"] == "explicit"


def test_unknown_explicit_model_returns_standard_error(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"model": "unknown", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert response.json()["error"]["param"] == "model"


def test_provider_filter_can_make_request_unavailable(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "routing": {"allowed_providers": ["openai"]},
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_provider_available"


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"stream": True},
        {"temperature": 2.1},
        {"max_tokens": 0},
        {"messages": []},
        {"unexpected": "value"},
        {"routing": {"allowed_providers": ["mock", "mock"]}},
    ],
)
def test_chat_request_validation(client: TestClient, invalid_fields: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "messages": [{"role": "user", "content": "Hello"}],
        **invalid_fields,
    }

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_chat_accepts_a_system_only_message(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "system", "content": "Be concise."}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Mock response: Be concise."
