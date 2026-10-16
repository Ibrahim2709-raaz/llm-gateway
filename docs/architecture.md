# MVP Architecture

## System context

```text
Client application
        |
        | OpenAI-compatible HTTPS request
        v
FastAPI gateway
        |-- Authentication and policy checks
        |-- Rate limiting and caching ------------ Redis
        |-- Routing, retries, and fallback
        |-- Usage and budget records ------------- PostgreSQL
        |-- Metrics ------------------------------- Prometheus -> Grafana
        |
        +-- OpenAI
        +-- Anthropic
        +-- Local OpenAI-compatible model
```

The gateway is the only component clients call. Provider-specific request and
response formats stay behind adapters.

## Request lifecycle

1. Validate the request and assign a request ID.
2. Authenticate the gateway API key.
3. Load customer permissions, limits, and budget.
4. Consume the request rate limit in Redis.
5. Check for an eligible cached response.
6. Estimate task complexity and request cost.
7. Rank eligible models according to the requested routing strategy.
8. Call the first provider with its timeout and retry policy.
9. Fall back to the next eligible provider after a retryable failure.
10. Normalize the provider response.
11. Calculate and persist actual token usage and cost.
12. Populate metrics and structured logs.
13. Cache the response when policy allows it.
14. Return the normalized response to the client.

## Initial component boundaries

| Component | Responsibility |
|---|---|
| API layer | HTTP validation, dependencies, and response formatting |
| Authentication | API-key verification and customer identity |
| Policy service | Permissions, rate limits, and budgets |
| Router | Complexity estimation, eligibility, scoring, and ordering |
| Provider adapters | Provider request conversion and response normalization |
| Execution service | Timeouts, retries, fallback, and circuit breakers |
| Cache service | Request hashing, isolation, storage, and expiry |
| Usage service | Token pricing, cost calculation, and persistence |
| Observability | Logs, metrics, request IDs, and redaction |

## Core data model

```text
Customer
  |-- ApiKey
  |-- Budget
  |-- UsageRecord

Provider
  |-- ModelProfile
  |-- ProviderAttempt

GatewayRequest
  |-- ProviderAttempt
  |-- UsageRecord
```

The model price records include effective dates so historical costs remain
reproducible after a provider changes its pricing.

## Important architectural rules

- API handlers coordinate services but do not contain provider logic.
- Provider adapters never decide which model should be selected.
- The router must not make network calls while calculating a route.
- Every provider attempt is observable, including failed attempts.
- Budget reservation and final usage settlement must be concurrency-safe.
- Cache keys are isolated by customer unless a future policy explicitly permits
  shared caching.
- API keys and provider credentials are never written to logs.
- Provider prices are configuration data, not routing constants in source code.
- The mock provider is a first-class adapter used by local development and tests.
- Real providers are opt-in; the gateway never enables a paid provider without
  both an explicit enable flag and a credential.
- Provider SDK retries are disabled so retry and fallback behavior remains
  observable and controlled by the gateway execution layer.
- Request limits use one atomic Redis script and customer-scoped keys. The
  gateway fails closed when Redis cannot make a safe limit decision.
- Automatic routing uses rule-based task classification and immutable model
  profiles for price, typical latency, quality, and maximum task complexity.
  Per-request allowlists and cost ceilings are applied before scoring.

## Technology choices

### FastAPI

Provides asynchronous HTTP handling, validation, dependency injection, and
generated OpenAPI documentation.

### PostgreSQL

Provides durable relational records and transactions for authentication,
budgets, pricing, and usage.

### Redis

Provides low-latency counters and expiring values for distributed rate limits,
caching, and circuit-breaker state.

### Prometheus and Grafana

Prometheus stores operational time-series metrics while Grafana supplies the
operator dashboard. PostgreSQL remains the source of truth for customer-level
usage and cost reporting.

## Deferred architecture decisions

The following decisions should be made when their implementation stage begins:

- Exact circuit-breaker thresholds
- Token-per-minute rate limiting
- Budget reservation strategy for streamed responses
- Shared versus private semantic caching
- Kubernetes and multi-region topology

