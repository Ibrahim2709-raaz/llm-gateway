# MVP Scope

## Product statement

The LLM Gateway gives an application one stable API for accessing several LLM
providers. It authenticates callers, controls traffic and spending, chooses an
eligible model, survives temporary provider failures, and records enough data to
explain the result and operate the service.

## Primary user

The first user is a developer or small engineering team that wants to use more
than one LLM provider without implementing provider selection, retries, budgets,
and monitoring in every application.

## MVP user stories

1. As a client developer, I can call one OpenAI-compatible chat endpoint.
2. As a platform operator, I can enable OpenAI, Anthropic, or a local
   OpenAI-compatible provider through configuration.
3. As a client developer, I can request `model: auto` and let the gateway choose
   an eligible model.
4. As a customer, my API key, permissions, rate limit, and monthly budget are
   enforced independently from other customers.
5. As a client developer, I receive a response from a fallback provider when the
   preferred provider has a temporary failure.
6. As a platform operator, I can see request volume, latency, errors, retries,
   fallbacks, token usage, and estimated cost.
7. As a platform operator, I can trace a request by its request ID without
   exposing secrets in logs.

## Included in the MVP

### API

- `POST /v1/chat/completions`
- Non-streaming text responses
- OpenAI-compatible message, choice, and usage fields
- `model: auto` plus explicit model selection
- Gateway-specific routing options for strategy, allowed providers, maximum
  cost, and cache bypass
- Consistent validation and error responses
- Liveness and readiness endpoints
- Monthly usage summary endpoint

### Providers

- Deterministic mock provider for tests and local development
- OpenAI adapter
- Anthropic adapter
- Generic OpenAI-compatible adapter for a local Ollama or vLLM server
- Normalized provider responses and errors

### Routing and resilience

- Rule-based task classification: simple, medium, or complex
- Cost, latency, quality, and balanced routing strategies
- Configurable provider timeouts
- Exponential retries for temporary errors
- Ordered fallback to another eligible provider
- Basic provider circuit breaker

### Access and cost controls

- Hashed API-key authentication
- Per-key provider and model permissions
- Redis-backed requests-per-minute limits
- Configurable monthly customer budgets
- Optional maximum cost per request
- Pre-request cost estimate and post-request actual cost calculation

### Storage and caching

- PostgreSQL for customers, credentials, budgets, model prices, requests, and
  provider attempts
- Redis for rate-limit counters, response cache, and short-lived provider state
- Exact-match response caching with customer isolation and configurable expiry

### Observability

- Structured JSON logs with request IDs
- Prometheus metrics
- A starter Grafana dashboard
- Metrics for traffic, errors, latency, retries, fallbacks, cache behavior,
  tokens, and cost
- Redaction of API keys and provider credentials

### Delivery

- Dockerfile and Docker Compose development environment
- Database migrations
- Unit, integration, and end-to-end tests using mock providers
- Continuous integration for linting, tests, and container builds
- Setup and API documentation

## Explicit non-goals for the MVP

These features are valuable, but they will not block the first release:

- Streaming responses
- Image, audio, embedding, or tool-calling endpoints
- A graphical customer administration portal
- Customer billing or payment processing
- Semantic or vector-based caching
- Machine-learning-based routing
- Kubernetes deployment
- Multi-region failover
- Provider fine-tuning management
- Full prompt-content logging or prompt analytics
- Guaranteed compatibility with every OpenAI API parameter

## Default product decisions

| Area | MVP decision |
|---|---|
| API compatibility | OpenAI-style chat completions |
| Response mode | Non-streaming |
| Routing | Transparent rules and weighted scores |
| Authentication | Gateway-issued API keys stored as hashes |
| Rate limit | Requests per minute per API key |
| Budget period | Calendar month in USD |
| Cache | Exact request match in Redis |
| Persistence | PostgreSQL through SQLAlchemy migrations |
| Local execution | Docker Compose |
| Prompt storage | Disabled by default |
| Pricing | Versioned configuration records |

## MVP acceptance criteria

The MVP is complete when all of the following are demonstrated:

- [x] The stack starts from a clean checkout with documented commands.
- [x] A valid API key can make a chat-completion request.
- [x] An invalid or revoked API key receives `401 Unauthorized`.
- [x] Two customers have independent rate limits, caches, and budgets.
- [x] `model: auto` selects a model and exposes the routing attempts in gateway
      metadata.
- [x] A simulated temporary provider failure triggers retry and fallback.
- [x] A permanent provider error is not retried.
- [x] A repeated cacheable request avoids a second provider call.
- [x] A request over its rate limit receives `429 Too Many Requests`.
- [x] A request over its budget is rejected before a paid provider call.
- [x] Successful provider calls store tokens, cost, latency, and status.
- [x] Prometheus exposes gateway and provider metrics.
- [x] Grafana shows traffic, latency, failures, fallback activity, and cost.
- [x] Logs can trace one request without revealing credentials.
- [x] Automated tests cover authentication, routing, fallback, caching, rate
      limiting, and budget enforcement.

## Definition of done for every feature

A feature is done only when it has:

- Implementation and configuration
- Automated tests for success and failure cases
- Structured logs and relevant metrics
- Documented behavior and example usage
- No credentials or sensitive prompt content in logs

