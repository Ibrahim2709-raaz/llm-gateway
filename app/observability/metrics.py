from typing import Protocol

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class MetricsRecorder(Protocol):
    def record_gateway_request(self, outcome: str, duration_seconds: float) -> None: ...

    def record_provider_attempt(self, provider: str, model: str, outcome: str) -> None: ...

    def record_fallback(self, from_provider: str, to_provider: str) -> None: ...

    def record_provider_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None: ...

    def record_cache_request(self, result: str) -> None: ...

    def record_rate_limit(self, decision: str) -> None: ...

    def record_shadow_decision(
        self,
        result: str,
        task_category: str,
        confidence: float,
        policy_version: str,
        score_version: str,
    ) -> None: ...

    def record_intelligent_route(
        self,
        provider: str,
        model: str,
        task_category: str,
        strategy: str,
        policy_version: str,
        score_version: str,
        estimated_cost_usd: float,
        actual_cost_usd: float,
    ) -> None: ...

    def record_routing_evidence(
        self,
        policy_version: str,
        score_version: str,
        expiration_timestamp_seconds: float,
    ) -> None: ...


class GatewayMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.registry = registry
        self.gateway_requests = Counter(
            "llm_gateway_requests_total",
            "Chat completion requests by final outcome.",
            ("outcome",),
            registry=registry,
        )
        self.gateway_request_duration = Histogram(
            "llm_gateway_request_duration_seconds",
            "End-to-end chat completion duration by final outcome.",
            ("outcome",),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
            registry=registry,
        )
        self.provider_attempts = Counter(
            "llm_gateway_provider_attempts_total",
            "Provider attempts by provider, model, and outcome.",
            ("provider", "model", "outcome"),
            registry=registry,
        )
        self.fallbacks = Counter(
            "llm_gateway_fallbacks_total",
            "Fallback route transitions between providers.",
            ("from_provider", "to_provider"),
            registry=registry,
        )
        self.tokens = Counter(
            "llm_gateway_tokens_total",
            "Provider-reported tokens by provider, model, and token type.",
            ("provider", "model", "type"),
            registry=registry,
        )
        self.cost_usd = Counter(
            "llm_gateway_cost_usd_total",
            "Provider-reported request cost in US dollars.",
            ("provider", "model"),
            registry=registry,
        )
        self.cache_requests = Counter(
            "llm_gateway_cache_requests_total",
            "Response cache requests by result.",
            ("result",),
            registry=registry,
        )
        self.rate_limit_decisions = Counter(
            "llm_gateway_rate_limit_decisions_total",
            "Rate-limit decisions by result.",
            ("decision",),
            registry=registry,
        )
        self.shadow_decisions = Counter(
            "llm_gateway_shadow_decisions_total",
            "Shadow routing comparisons by result and versioned task context.",
            ("result", "task_category", "policy_version", "score_version"),
            registry=registry,
        )
        self.classifier_confidence = Histogram(
            "llm_gateway_classifier_confidence",
            "Classifier confidence observed during shadow routing.",
            ("task_category",),
            buckets=(0.0, 0.25, 0.5, 0.65, 0.75, 0.85, 0.95, 1.0),
            registry=registry,
        )
        self.intelligent_routes = Counter(
            "llm_gateway_intelligent_routes_total",
            "Executed intelligent routes by model, task, strategy, and evidence versions.",
            (
                "provider",
                "model",
                "task_category",
                "strategy",
                "policy_version",
                "score_version",
            ),
            registry=registry,
        )
        self.intelligent_estimated_cost_usd = Counter(
            "llm_gateway_intelligent_estimated_cost_usd_total",
            "Estimated provider cost for executed intelligent routes.",
            ("provider", "model", "strategy"),
            registry=registry,
        )
        self.intelligent_actual_cost_usd = Counter(
            "llm_gateway_intelligent_actual_cost_usd_total",
            "Actual provider cost for executed intelligent routes.",
            ("provider", "model", "strategy"),
            registry=registry,
        )
        self.routing_evidence_expiration_timestamp_seconds = Gauge(
            "llm_gateway_routing_evidence_expiration_timestamp_seconds",
            "Unix timestamp when the earliest active capability score expires.",
            ("policy_version", "score_version"),
            registry=registry,
        )

    def record_gateway_request(self, outcome: str, duration_seconds: float) -> None:
        self.gateway_requests.labels(outcome=outcome).inc()
        self.gateway_request_duration.labels(outcome=outcome).observe(max(0.0, duration_seconds))

    def record_provider_attempt(self, provider: str, model: str, outcome: str) -> None:
        self.provider_attempts.labels(
            provider=provider,
            model=model,
            outcome=outcome,
        ).inc()

    def record_fallback(self, from_provider: str, to_provider: str) -> None:
        self.fallbacks.labels(
            from_provider=from_provider,
            to_provider=to_provider,
        ).inc()

    def record_provider_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        self.tokens.labels(provider=provider, model=model, type="input").inc(max(0, input_tokens))
        self.tokens.labels(provider=provider, model=model, type="output").inc(max(0, output_tokens))
        self.cost_usd.labels(provider=provider, model=model).inc(max(0.0, cost_usd))

    def record_cache_request(self, result: str) -> None:
        self.cache_requests.labels(result=result).inc()

    def record_rate_limit(self, decision: str) -> None:
        self.rate_limit_decisions.labels(decision=decision).inc()

    def record_shadow_decision(
        self,
        result: str,
        task_category: str,
        confidence: float,
        policy_version: str,
        score_version: str,
    ) -> None:
        self.shadow_decisions.labels(
            result=result,
            task_category=task_category,
            policy_version=policy_version,
            score_version=score_version,
        ).inc()
        self.classifier_confidence.labels(task_category=task_category).observe(
            min(1.0, max(0.0, confidence))
        )

    def record_intelligent_route(
        self,
        provider: str,
        model: str,
        task_category: str,
        strategy: str,
        policy_version: str,
        score_version: str,
        estimated_cost_usd: float,
        actual_cost_usd: float,
    ) -> None:
        self.intelligent_routes.labels(
            provider=provider,
            model=model,
            task_category=task_category,
            strategy=strategy,
            policy_version=policy_version,
            score_version=score_version,
        ).inc()
        cost_labels = {"provider": provider, "model": model, "strategy": strategy}
        self.intelligent_estimated_cost_usd.labels(**cost_labels).inc(
            max(0.0, estimated_cost_usd)
        )
        self.intelligent_actual_cost_usd.labels(**cost_labels).inc(max(0.0, actual_cost_usd))

    def record_routing_evidence(
        self,
        policy_version: str,
        score_version: str,
        expiration_timestamp_seconds: float,
    ) -> None:
        self.routing_evidence_expiration_timestamp_seconds.labels(
            policy_version=policy_version,
            score_version=score_version,
        ).set(expiration_timestamp_seconds)

    def render(self) -> bytes:
        return generate_latest(self.registry)


METRICS = GatewayMetrics()
