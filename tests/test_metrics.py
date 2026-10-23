from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.observability.metrics import GatewayMetrics


def test_gateway_metrics_export_bounded_operational_labels() -> None:
    registry = CollectorRegistry()
    metrics = GatewayMetrics(registry)

    metrics.record_gateway_request("success", 0.125)
    metrics.record_provider_attempt("openai", "example-model", "success")
    metrics.record_fallback("openai", "local")
    metrics.record_provider_usage("openai", "example-model", 10, 5, 0.0025)
    metrics.record_cache_request("hit")
    metrics.record_rate_limit("allowed")

    assert registry.get_sample_value("llm_gateway_requests_total", {"outcome": "success"}) == 1
    assert (
        registry.get_sample_value(
            "llm_gateway_provider_attempts_total",
            {"provider": "openai", "model": "example-model", "outcome": "success"},
        )
        == 1
    )
    assert (
        registry.get_sample_value(
            "llm_gateway_tokens_total",
            {"provider": "openai", "model": "example-model", "type": "input"},
        )
        == 10
    )
    assert (
        registry.get_sample_value(
            "llm_gateway_cost_usd_total",
            {"provider": "openai", "model": "example-model"},
        )
        == 0.0025
    )
    rendered = metrics.render().decode()
    assert "llm_gateway_request_duration_seconds_bucket" in rendered
    assert "client_id" not in rendered
    assert "request_id" not in rendered


def test_metrics_endpoint_is_public_for_prometheus(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "llm_gateway_requests_total" in response.text
