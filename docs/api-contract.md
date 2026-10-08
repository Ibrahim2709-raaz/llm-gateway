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
Authorization: Bearer gw_live_example
```

Provider credentials are configured on the gateway and are never supplied by
clients. Every endpoint except health checks and Prometheus metrics requires a
gateway API key.

## Request identity

The gateway assigns every request a UUID. A client may supply `X-Request-ID`
when it already has a valid UUID; otherwise, the gateway generates one.

Every response includes:

```http
X-Request-ID: 0a8b43e4-47f0-4eb7-b5a1-8f56d1dc40d5
```

The same value appears in success metadata, errors, structured logs, and usage
records.

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
- `temperature` is between `0` and `2`.
- `max_tokens` is between `1` and `32768`, subject to the selected model's
  context limit.
- `stream` must be `false` in the MVP.
- `allowed_providers` can only reduce the customer's permitted providers.
- `max_cost_usd` is a hard limit. The router removes candidates whose estimated
  cost exceeds it.
- `disable_cache` bypasses both cache reads and writes.

### Example response

```json
{
  "id": "chatcmpl_01jexample",
  "object": "chat.completion",
  "created": 1790707200,
  "model": "gpt-4.1-mini",
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
    "provider": "openai",
    "cost_usd": 0.000081,
    "cached": false,
    "routing_strategy": "balanced",
    "attempts": [
      {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "outcome": "success"
      }
    ]
  }
}
```

Only sanitized attempt information is returned. Internal exception messages and
provider credentials are never exposed to clients.

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
| 422 | `validation_error` | A field has the wrong type, range, or shape. |
| 429 | `rate_limit_exceeded` | The customer's request limit is exhausted. |
| 502 | `provider_error` | All attempts ended with non-timeout provider failures. |
| 503 | `no_provider_available` | No healthy candidate satisfies the request policy. |
| 504 | `provider_timeout` | All eligible provider attempts timed out. |

Rate-limit responses include `Retry-After` in seconds. Internal errors use a
generic client message while retaining diagnostic detail in protected logs.

## Cache behavior

A response is cache-eligible only when policy allows it. The cache identity
includes the customer, messages, explicit model or routing policy, temperature,
token limit, and all output-affecting options.

A cache hit:

- Returns a new gateway request ID.
- Preserves the original model and provider fields.
- Sets `gateway.cached` to `true`.
- Does not create a paid-provider usage charge.
- Still counts against request-rate limits.

## Processing guarantees

- The gateway performs at most one active provider attempt for a request at a
  time in the MVP.
- Retries and fallbacks never select a provider outside customer permissions.
- Budget and per-request cost checks apply to every fallback candidate.
- A provider call is recorded even when it fails.
- A successful client response is returned only after its usage record is
  durably stored.
- Request bodies are not logged by default.

