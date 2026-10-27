#!/usr/bin/env python3
import argparse
import json
import math
import os
import time
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


class SmokeTestError(RuntimeError):
    pass


def request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    api_key: str | None = None,
    payload: dict[str, Any] | None = None,
    expected_status: int = 200,
) -> tuple[dict[str, Any] | str, dict[str, str]]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    http_request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(http_request, timeout=10) as response:
            status = response.status
            response_body = response.read().decode()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as error:
        status = error.code
        response_body = error.read().decode()
        response_headers = {key.lower(): value for key, value in error.headers.items()}
    except URLError as error:
        raise SmokeTestError(f"Could not reach {path}: {error.reason}") from error

    if status != expected_status:
        raise SmokeTestError(
            f"{method} {path} returned {status}, expected {expected_status}: "
            f"{response_body[:500]}"
        )
    content_type = response_headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return json.loads(response_body), response_headers
        except json.JSONDecodeError as error:
            raise SmokeTestError(f"{path} returned invalid JSON") from error
    return response_body, response_headers


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def wait_for_readiness(base_url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "gateway did not respond"
    while time.monotonic() < deadline:
        try:
            payload, _ = request(base_url, "/health/ready")
            if isinstance(payload, dict) and payload.get("status") == "ready":
                return
            last_error = f"unexpected readiness response: {payload}"
        except SmokeTestError as error:
            last_error = str(error)
        time.sleep(1)
    raise SmokeTestError(f"Gateway was not ready after {timeout_seconds}s: {last_error}")


def assert_completion(
    completion: dict[str, Any] | str,
    *,
    cached: bool,
    label: str,
) -> dict[str, Any]:
    if not isinstance(completion, dict):
        raise SmokeTestError(f"{label} completion was not JSON")
    require(completion.get("model") == "mock-1", f"{label} did not select the mock model")
    require(
        completion.get("gateway", {}).get("provider") == "mock",
        f"{label} did not use the mock provider",
    )
    require(
        completion.get("gateway", {}).get("cached") is cached,
        f"{label} returned an unexpected cache state",
    )
    return completion


def assert_budget(usage: dict[str, Any], expected_budget_usd: float | None, label: str) -> None:
    if expected_budget_usd is None:
        return
    budget = usage.get("budget_usd")
    require(
        isinstance(budget, (int, float)) and math.isclose(budget, expected_budget_usd),
        f"{label} reported budget {budget!r}, expected {expected_budget_usd}",
    )


def run(
    base_url: str,
    api_key: str,
    timeout_seconds: int,
    *,
    secondary_api_key: str | None = None,
    expected_budget_usd: float | None = None,
    secondary_expected_budget_usd: float | None = None,
) -> None:
    wait_for_readiness(base_url, timeout_seconds)

    liveness, live_headers = request(base_url, "/health/live")
    require(isinstance(liveness, dict) and liveness.get("status") == "ok", "Liveness failed")
    require("x-request-id" in live_headers, "Liveness response has no request ID")

    unauthorized, unauthorized_headers = request(
        base_url,
        "/v1/chat/completions",
        method="POST",
        api_key=f"gw_live_{'A' * 43}",
        payload={"model": "mock-1", "messages": [{"role": "user", "content": "test"}]},
        expected_status=401,
    )
    require(
        isinstance(unauthorized, dict)
        and unauthorized.get("error", {}).get("code") == "invalid_api_key",
        "Invalid API key did not return the expected error",
    )
    require(
        unauthorized_headers.get("www-authenticate") == "Bearer",
        "Unauthorized response has no Bearer challenge",
    )

    nonce = uuid4().hex
    completion_request = {
        "model": "mock-1",
        "messages": [{"role": "user", "content": f"Release smoke test {nonce}"}],
        "temperature": 0,
        "max_tokens": 64,
    }
    first, first_headers = request(
        base_url,
        "/v1/chat/completions",
        method="POST",
        api_key=api_key,
        payload=completion_request,
    )
    first = assert_completion(first, cached=False, label="First primary")
    require(
        first.get("gateway", {}).get("attempts", [{}])[-1].get("outcome") == "success",
        "First completion has no successful provider attempt",
    )
    expected_headers = (
        "x-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    )
    for header in expected_headers:
        require(header in first_headers, f"Completion response has no {header} header")

    second, second_headers = request(
        base_url,
        "/v1/chat/completions",
        method="POST",
        api_key=api_key,
        payload=completion_request,
    )
    second = assert_completion(second, cached=True, label="Second primary")
    require(second.get("gateway", {}).get("cost_usd") == 0, "Cache hit reported a cost")
    require(second.get("gateway", {}).get("attempts") == [], "Cache hit called a provider")
    require(
        first.get("choices") == second.get("choices"),
        "Cached completion content does not match the original",
    )
    require(
        first_headers["x-request-id"] != second_headers.get("x-request-id"),
        "Cache hit reused the original request ID",
    )

    if secondary_api_key is not None:
        secondary_first, secondary_first_headers = request(
            base_url,
            "/v1/chat/completions",
            method="POST",
            api_key=secondary_api_key,
            payload=completion_request,
        )
        secondary_first = assert_completion(
            secondary_first,
            cached=False,
            label="First secondary",
        )
        require(
            secondary_first.get("choices") == first.get("choices"),
            "Secondary customer received different deterministic mock content",
        )
        require(
            int(secondary_first_headers["x-ratelimit-remaining"])
            > int(second_headers["x-ratelimit-remaining"]),
            "Secondary customer did not receive an independent rate-limit counter",
        )

        secondary_second, _ = request(
            base_url,
            "/v1/chat/completions",
            method="POST",
            api_key=secondary_api_key,
            payload=completion_request,
        )
        assert_completion(secondary_second, cached=True, label="Second secondary")

    period = datetime.now(UTC).strftime("%Y-%m")
    usage_path = f"/v1/usage?{urlencode({'period': period})}"
    usage, _ = request(base_url, usage_path, api_key=api_key)
    if not isinstance(usage, dict):
        raise SmokeTestError("Usage summary was not JSON")
    require(usage.get("request_count", 0) >= 2, "Usage summary omitted smoke-test requests")
    require(usage.get("total_tokens", 0) > 0, "Usage summary omitted provider tokens")
    assert_budget(usage, expected_budget_usd, "Primary customer")

    if secondary_api_key is not None:
        secondary_usage, _ = request(base_url, usage_path, api_key=secondary_api_key)
        if not isinstance(secondary_usage, dict):
            raise SmokeTestError("Secondary usage summary was not JSON")
        require(
            secondary_usage.get("request_count", 0) >= 2,
            "Secondary usage summary omitted smoke-test requests",
        )
        require(
            secondary_usage.get("total_tokens", 0) > 0,
            "Secondary usage summary omitted provider tokens",
        )
        assert_budget(
            secondary_usage,
            secondary_expected_budget_usd,
            "Secondary customer",
        )

    metrics, _ = request(base_url, "/metrics")
    require(isinstance(metrics, str), "Metrics response was not text")
    for metric in (
        "llm_gateway_requests_total",
        "llm_gateway_provider_attempts_total",
        "llm_gateway_cache_requests_total",
        "llm_gateway_rate_limit_decisions_total",
    ):
        require(metric in metrics, f"Metrics response omitted {metric}")

    checked = "auth, health, completion, cache, usage, and metrics"
    if secondary_api_key is not None:
        checked += ", plus customer isolation"
    print(f"End-to-end smoke test passed: {checked}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a running LLM Gateway stack.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("GATEWAY_BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument("--api-key", default=os.getenv("GATEWAY_API_KEY"))
    parser.add_argument("--secondary-api-key", default=os.getenv("GATEWAY_SECONDARY_API_KEY"))
    parser.add_argument("--expected-budget-usd", type=float)
    parser.add_argument("--secondary-expected-budget-usd", type=float)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    arguments = parser.parse_args()
    if not arguments.api_key:
        parser.error("--api-key or GATEWAY_API_KEY is required")
    if arguments.secondary_expected_budget_usd is not None and not arguments.secondary_api_key:
        parser.error("--secondary-expected-budget-usd requires --secondary-api-key")
    return arguments


def main() -> None:
    arguments = parse_args()
    try:
        run(
            arguments.base_url,
            arguments.api_key,
            arguments.timeout_seconds,
            secondary_api_key=arguments.secondary_api_key,
            expected_budget_usd=arguments.expected_budget_usd,
            secondary_expected_budget_usd=arguments.secondary_expected_budget_usd,
        )
    except SmokeTestError as error:
        raise SystemExit(f"Smoke test failed: {error}") from error


if __name__ == "__main__":
    main()
