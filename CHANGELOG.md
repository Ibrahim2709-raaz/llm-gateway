# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2026-10-26

### Added

- OpenAI-compatible chat-completions and monthly usage APIs.
- OpenAI, Anthropic, local OpenAI-compatible, and deterministic mock providers.
- API-key authentication and customer-isolated rate limits, caches, usage, and
  monthly budgets.
- Rule-based automatic routing by cost, latency, quality, or balanced strategy.
- Provider retries, ordered fallback, timeouts, and circuit breakers.
- Token-based provider cost estimation, budget reservation, and actual-cost
  settlement.
- Structured request logging with credential redaction and request IDs.
- Prometheus metrics and a provisioned Grafana operations dashboard.
- PostgreSQL migrations, Redis integration, Docker Compose, and production
  deployment and rollback documentation.
- Unit, integration, container-build, and multi-customer end-to-end CI checks.
- A detailed roadmap for evidence-driven, task-aware intelligent routing.

### Security

- Provider SDK retries are disabled so gateway retry behavior remains bounded
  and observable.
- Production mode supports trusted-host validation, request-body limits,
  defensive response headers, and disabled interactive API documentation.
- API keys, provider credentials, prompts, and response bodies are excluded from
  metrics and structured logs.

[0.1.0]: https://github.com/Ibrahim2709-raaz/llm-gateway/releases/tag/v0.1.0
