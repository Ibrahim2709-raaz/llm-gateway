# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Versioned task taxonomy, classifier metadata, evaluation datasets, resumable
  evaluation runs, and deterministic scoring.
- Audited activation and rollback for routing policies and model capability
  score sets, with freshness and minimum-sample enforcement.
- Evidence-driven task-aware routing using measured quality, cost, latency,
  reliability, capability requirements, and customer policy.
- Offline replay and non-disruptive shadow mode that never makes a second paid
  provider call.
- Stable customer canary cohorts, internal-customer allowlists, pinned policy
  and score versions, and an immediate `baseline-v1` global rollback switch.
- Versioned routing explanations in API metadata plus Grafana panels for task
  mix, model selection, latency, cost drift, evidence expiry, and cache savings.
- Prometheus routing alerts and an incident, promotion, and rollback runbook.

### Changed

- Package and OpenAPI version advanced to 0.2.0 for the intelligent-routing
  release candidate.
- CI now validates Prometheus rules and enforces at least 90% test coverage for
  the cumulative routing implementation.

### Security

- Production prompts remain excluded from evaluation storage, metrics, and
  routing logs by default.
- Canary customers bypass the baseline response cache until cache keys include
  the active policy and score versions, preventing cross-policy cache reuse.

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
