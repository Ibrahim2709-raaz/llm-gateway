# API Contract

This document defines the MVP HTTP interface. The machine-readable companion is
[`openapi.yaml`](openapi.yaml).

## Compatibility policy

The chat endpoint follows the commonly used OpenAI chat-completions shape for
messages, choices, and token usage. Gateway-specific request controls use the
`routing` object, and gateway-specific response details use the `gateway`
object. Clients that ignore unknown response fields can continue using the
standard completion fields.

The MVP supports non-streaming text completions only. A request with
`stream: true` receives a validation error.

## Authentication

Clients send a gateway-issued API key as a bearer token:

```http
Authorization: Bearer gw_live_<43-character-secret>
```

Provider credentials are configured on the gateway and are never supplied by
clients. Every endpoint except health checks and Prometheus metrics requires a
gateway API key.

Create a client and its first key after applying database migrations:

```bash
llm-gateway-create-key --client-name example --key-name production
```

The plaintext key is displayed once. The database stores only a visible prefix
and an HMAC-SHA256 digest created with `GATEWAY_API_KEY_PEPPER`. Missing,
malformed, unknown, revoked, and inactive-client keys all receive the same
generic `401 invalid_api_key` response so the endpoint does not reveal account
state.

## Request identity

The gateway assigns every request a UUID. A client may supply `X-Request-ID`
when it already has a valid UUID; otherwise, the gateway generates one.

Every response includes:

```http
X-Request-ID: 0a8b43e4-47f0-4eb7-b5a1-8f56d1dc40d5
```

The same value appears in success metadata, errors, structured logs, and usage
records.

## Request rate limits

The chat endpoint consumes one request from a Redis-backed fixed window for the
authenticated customer. The default policy is 60 requests per 60 seconds and
can be configured with `REQUEST_RATE_LIMIT` and
`REQUEST_RATE_LIMIT_WINDOW_SECONDS`.

Accepted responses include:

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 59
X-RateLimit-Reset: 1790707260
```

`X-RateLimit-Reset` is the Unix timestamp when the current window expires. A
rejected request returns `429 rate_limit_exceeded`, the same three headers, and
`Retry-After` in seconds. Limits are isolated by customer rather than API key,
so rotating or creating another key does not bypass the policy. If Redis cannot
make an atomic decision, the gateway fails closed with
`503 rate_limit_unavailable` to avoid uncontrolled provider spending.

## Create a chat completion

```http
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer gw_live_example
```

### Example request

```json
{
  "model": "auto",
  "messages": [
    {
      "role": "system",
      "content": "Answer clearly and concisely."
    },
    {
      "role": "user",
      "content": "Explain why Redis is useful for rate limiting."
    }
  ],
  "temperature": 0.2,
  "max_tokens": 400,
  "stream": false,
  "routing": {
    "strategy": "balanced",
    "allowed_providers": ["openai", "anthropic", "local"],
    "max_cost_usd": 0.05,
    "disable_cache": false
  }
}
```

### Request rules

- `model` defaults to `auto`. An explicit model must be enabled and permitted
  for the authenticated customer.
- `messages` must contain at least one message and cannot exceed the configured
  request-size limit.
- `temperature` is between `0` and `2`. An adapter may omit it when the selected
  provider/model does not support sampling parameters; the Anthropic adapter
  does this for current Claude models. The OpenAI and local OpenAI-compatible
  adapters forward it.
- `max_tokens` is between `1` and `32768`, subject to the selected model's
  context limit.
- `stream` must be `false` in the MVP.
- `allowed_providers` can only reduce the customer's permitted providers.
- `max_cost_usd` is a hard limit. The router removes candidates whose estimated
  cost exceeds it.
- `disable_cache` bypasses both cache reads and writes.

For `model: "auto"`, the gateway classifies the task as `simple`, `medium`, or
`complex`. It removes candidates that violate provider allowlists, task
capability, or the maximum estimated request cost. The selected strategy then
optimizes estimated price (`cost`), typical response time (`latency`), model
capability (`quality`), or a complexity-aware weighted score (`balanced`). An
explicit model bypasses ranking but still respects allowlists and cost limits.

### Retries and fallback

Timeouts, upstream rate limits, and temporary availability failures are
retryable. The gateway retries the same provider with capped exponential
backoff, then advances through the remaining ranked candidates. Every fallback
candidate already satisfies the request's provider allowlist, task capability,
and estimated-cost ceiling. Provider request errors are treated as permanent:
they return immediately and are never retried or fallen back.

`gateway.attempts` preserves the execution order and reports only sanitized
outcomes: `success`, `timeout`, `rate_limited`, `unavailable`, `circuit_open`,
or `error`. A `circuit_open` attempt means the provider/model was skipped during
its recovery cooldown, without making an upstream call. If every eligible
attempt times out, the gateway returns `504 provider_timeout`; other exhausted
retryable routes return `502 provider_error`.

### Example response

```json
{
  "id": "chatcmpl_01jexample",
  "object": "chat.completion",
  "created": 1790707200,
  "model": "claude-sonnet-5-5",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Redis provides fast atomic counters with automatic expiry..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 31,
    "completion_tokens": 67,
    "total_tokens": 98
  },
  "gateway": {
    "request_id": "0a8b43e4-47f0-4eb7-b5a1-8f56d1dc40d5",
    "provider": "anthropic",
    "cost_usd": 0.000081,
    "cached": false,
    "routing_strategy": "balanced",
    "task_complexity": "medium",
    "task_category": "reasoning",
    "classification_confidence": 0.86,
    "classifier_version": "task-rules-v1",
    "classification_reasons": [
      "category_reasoning_signal",
      "difficulty_medium_signal"
    ],
    "estimated_cost_usd": 0.004862,
    "attempts": [
      {
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "outcome": "timeout"
      },
      {
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "outcome": "timeout"
      },
      {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "outcome": "success"
      }
    ]
  }
}
```

`estimated_cost_usd` is the pre-request upper-bound estimate based on estimated
input tokens and the requested maximum output. `cost_usd` is the actual
provider-reported usage cost. Only sanitized attempt information is returned;
internal exception messages and provider credentials are never exposed.
Classification explanations contain only versioned reason codes and never copy
prompt text into response metadata.

## Usage summary

```http
GET /v1/usage?period=2026-10
Authorization: Bearer gw_live_example
```

Example response:

```json
{
  "period": "2026-10",
  "request_count": 142,
  "prompt_tokens": 120450,
  "completion_tokens": 45320,
  "total_tokens": 165770,
  "cost_usd": 3.8241,
  "budget_usd": 25.0,
  "remaining_budget_usd": 21.1759
}
```

The endpoint only returns usage belonging to the authenticated customer.
`request_count` includes successful provider responses and cache hits. Cached
requests contribute zero tokens and cost, while failed and in-flight requests
are excluded from reported usage. When no monthly budget is configured,
`budget_usd` and `remaining_budget_usd` are `null`.

Before a provider call, the gateway locks the customer's monthly budget row and
durably reserves the highest estimated cost among eligible fallback candidates.
This prevents concurrent requests from independently spending the same
remaining balance. A successful call replaces the reservation with actual
provider-reported tokens and cost; failures release it. Durable records also
snapshot provider, model, latency, and status. Requests that would exceed the
remaining budget receive `402 budget_exceeded` before any provider call.

Operators create or update a monthly limit with:

```bash
llm-gateway-set-budget \
  --client-id <client-uuid> \
  --period 2026-10 \
  --limit-usd 25 \
  --alert-threshold-percent 80
```

## Health and metrics

### Liveness

`GET /health/live` reports whether the process is running. It does not contact
dependencies.

### Readiness

`GET /health/ready` verifies that PostgreSQL and Redis can serve requests.
Readiness returns `503` when either required dependency is unavailable.

### Metrics

`GET /metrics` exposes Prometheus text metrics. It does not include raw customer
IDs, API keys, prompts, or response content.

The gateway publishes these metric families:

- `llm_gateway_requests_total` and `llm_gateway_request_duration_seconds`
- `llm_gateway_provider_attempts_total` and `llm_gateway_fallbacks_total`
- `llm_gateway_cache_requests_total`
- `llm_gateway_rate_limit_decisions_total`
- `llm_gateway_tokens_total` and `llm_gateway_cost_usd_total`

Labels are intentionally limited to operational dimensions such as provider,
model, outcome, token type, and cache/rate-limit result.

## Error format

All API errors use one envelope:

```json
{
  "error": {
    "message": "Request rate limit exceeded.",
    "type": "rate_limit_error",
    "param": null,
    "code": "rate_limit_exceeded",
    "request_id": "0a8b43e4-47f0-4eb7-b5a1-8f56d1dc40d5"
  }
}
```

| Status | Code | Meaning |
|---:|---|---|
| 400 | `invalid_request` | The request is syntactically valid but violates a gateway rule. |
| 401 | `invalid_api_key` | The key is missing, invalid, or revoked. |
| 402 | `budget_exceeded` | The estimated request would exceed the available budget. |
| 403 | `model_not_allowed` | The customer cannot use the requested model or provider. |
| 404 | `model_not_found` | The explicit model is unknown or disabled. |
| 413 | `request_too_large` | The request body exceeds `MAX_REQUEST_BODY_BYTES`. |
| 422 | `validation_error` | A field has the wrong type, range, or shape. |
| 429 | `rate_limit_exceeded` | The customer's request limit is exhausted. |
| 502 | `provider_error` | All attempts ended with non-timeout provider failures. |
| 503 | `no_provider_available` | No healthy candidate satisfies the request policy. |
| 503 | `rate_limit_unavailable` | Redis could not make a safe rate-limit decision. |
| 504 | `provider_timeout` | All eligible provider attempts timed out. |

Rate-limit responses include `Retry-After` in seconds. Internal errors use a
generic client message while retaining diagnostic detail in protected logs.

## Cache behavior

A response is cache-eligible only when policy allows it. The cache identity
includes the customer, messages, explicit model or routing policy, temperature,
token limit, and all output-affecting options.

A cache hit:

- Returns a new gateway request ID.
- Returns a new completion ID and response timestamp.
- Preserves the original model and provider fields.
- Sets `gateway.cached` to `true`.
- Sets `gateway.cost_usd` to `0` and returns an empty attempt list because no
  provider was called for the current request.
- Does not create a paid-provider usage charge.
- Still counts against request-rate limits.

Successful exact-match responses expire after `RESPONSE_CACHE_TTL_SECONDS`
(300 seconds by default). Cache keys are versioned, include the customer UUID,
and contain a SHA-256 digest rather than raw prompts. Provider allowlists are
canonicalized so their order does not create duplicate entries. Setting
`routing.disable_cache` bypasses both reads and writes. Redis cache failures
fail open: the request proceeds through routing and provider execution while a
sanitized warning is logged.

## Processing guarantees

- The gateway performs at most one active provider attempt for a request at a
  time in the MVP.
- Retries and fallbacks never select a provider outside customer permissions.
- Budget and per-request cost checks apply to every fallback candidate.
- A provider call is recorded even when it fails.
- A successful client response is returned only after its usage record is
  durably stored.
- Request bodies are not logged by default.

