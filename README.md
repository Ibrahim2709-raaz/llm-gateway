# LLM Gateway

[![CI](https://github.com/Ibrahim2709-raaz/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Ibrahim2709-raaz/llm-gateway/actions/workflows/ci.yml)

A multi-provider LLM API gateway that routes requests according to cost, latency,
availability, and task complexity.

## Version 0.1 capabilities

- OpenAI-compatible chat-completions API
- OpenAI, Anthropic, and local-model providers
- API-key authentication and per-client permissions
- Redis-backed rate limiting and response caching
- Automatic retries and provider fallback
- Usage budgets and token-cost tracking
- Structured logs and request tracing
- Prometheus metrics and Grafana dashboards
- Docker-based local development environment

## Stack

- Python and FastAPI
- PostgreSQL
- Redis
- Docker Compose
- Prometheus and Grafana

## Release status

Version 0.1 is the stable gateway baseline. It implements the unified API,
OpenAI, Anthropic, local OpenAI-compatible, and mock providers, API-key
authentication, distributed rate limiting, rule-based routing, retries and
fallback, response caching, usage budgets, cost tracking, Prometheus metrics, a
provisioned Grafana dashboard, production-oriented API hardening, automated CI,
and a full-stack release smoke test.

The reliability layer includes a process-local provider circuit breaker that
stops retrying an unhealthy model, routes immediately to the next candidate
during the cooldown, and permits one half-open recovery probe before restoring
normal traffic. The API also enforces a request-body ceiling, adds defensive
response headers, validates trusted hosts, and disables interactive API
documentation in production.

GitHub Actions validates every pull request and every push to `main` by running
Ruff, mypy, the full coverage-enforced pytest suite, a Docker image build, and a
clean-checkout multi-customer smoke test.

## Next phase: evidence-driven intelligent routing

The next development phase evolves the rule-based router into a task-aware,
evaluation-driven model-selection system. Work will follow the committed
[intelligent model routing roadmap](docs/intelligent-routing-roadmap.md):

1. Define a versioned task taxonomy and routing data model.
2. Classify requests by task category, difficulty, and confidence.
3. Benchmark each model with reproducible category-specific evaluations.
4. Route using measured quality together with cost, latency, and reliability.
5. Validate decisions in shadow mode before gradual production rollout.
6. Explain model selections in API metadata and Grafana dashboards.

On the intelligent-routing development branch, operators can initialize and
inspect the versioned foundation without exposing it through a customer API:

```bash
alembic upgrade head
llm-gateway-routing-foundation seed
llm-gateway-routing-foundation show
# Promote a reviewed draft policy:
llm-gateway-routing-foundation activate candidate-v1 --actor release-bot --reason "gates passed"
# Return to a previously active policy:
llm-gateway-routing-foundation rollback baseline-v1 --actor on-call --reason "rollback"
```

The seed is idempotent. It records the released `baseline-v1` policy and creates
the initial task-taxonomy dataset in draft state; it does not store production
prompts or invent capability scores before evaluations exist.
Policy activation is transactional, permits only one active version, and records
an immutable operator audit event. `show` includes recent activation history.

Milestone Two includes a deterministic classifier inspection command. It reports
the primary task category, difficulty, confidence, matched safe signals, reason
codes, and classifier version without calling an LLM or storing the prompt:

```bash
llm-gateway-classify "Summarize this Python function and list the key points"
llm-gateway-classifier-eval --split heldout
```

The classifier passed the initial labeled validation gate and now supplies task
difficulty to automatic routing plus safe classification metadata in responses.
Explicit-model requests remain exempt from automatic difficulty filtering.
The versioned dataset and thresholds are documented in
[the classifier dataset card](docs/classifier-dataset.md).

The evaluation harness can be demonstrated without paid provider credentials:

```bash
alembic upgrade head
llm-gateway-eval seed-smoke
llm-gateway-eval create --provider mock --model mock-1
llm-gateway-eval run <RUN_ID>
llm-gateway-eval show <RUN_ID>
llm-gateway-eval publish-scores <RUN_ID> --score-version mock-smoke-v1
```

Use `llm-gateway-eval resume <RUN_ID>` after an interrupted run. Each completed
case is committed independently, so resume skips immutable existing results.
Publishing scores is allowed only for a fully completed run with no failed cases;
it creates a draft score version and never activates routing automatically.
After operator review, activate a sufficiently sampled score version with a
bounded freshness window:

```bash
llm-gateway-eval compare-scores --provider openai --model MODEL \
  --score-version VERSION
llm-gateway-eval activate-scores --provider openai --model MODEL \
  --score-version VERSION --actor release-bot --valid-days 30
llm-gateway-eval rollback-scores --provider openai --model MODEL \
  --score-version PREVIOUS_VERSION --actor on-call
```

## Project documentation

- [MVP scope and acceptance criteria](docs/mvp-scope.md)
- [MVP architecture](docs/architecture.md)
- [API contract and examples](docs/api-contract.md)
- [OpenAPI 3.1 specification](docs/openapi.yaml)
- [Production deployment and rollback guide](docs/deployment.md)
- [Intelligent model routing roadmap](docs/intelligent-routing-roadmap.md)

## Run locally

Requirements: Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open:

- API documentation: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health/live>
- Readiness: <http://localhost:8000/health/ready>
- Prometheus metrics: <http://localhost:8000/metrics>

Try the mock completion endpoint:

```bash
llm-gateway-create-key --client-name demo

curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <the-key-printed-above>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Explain an LLM gateway."}]}'
```

Run `alembic upgrade head` before creating a key. The command prints the API
key and customer ID once; save the key securely because the gateway stores only
a visible prefix and an HMAC-SHA256 digest. Set `GATEWAY_API_KEY_PEPPER` to a
stable secret in every environment, and never change it without rotating all
API keys. The development default is rejected when `APP_ENV=production`.

Each authenticated customer is allowed 60 requests per 60-second window by
default. Configure this with `REQUEST_RATE_LIMIT` and
`REQUEST_RATE_LIMIT_WINDOW_SECONDS`. Successful responses include limit,
remaining, and reset headers; rejected requests return `429` and `Retry-After`.
The gateway fails closed with `503 rate_limit_unavailable` if Redis cannot make
a safe rate-limit decision.

Retryable provider failures open the provider/model circuit after five
consecutive failures by default. Configure the threshold and cooldown with
`PROVIDER_CIRCUIT_BREAKER_FAILURE_THRESHOLD` and
`PROVIDER_CIRCUIT_BREAKER_RECOVERY_SECONDS`. Requests skipped by an open circuit
appear as `circuit_open` routing attempts and automatically fall back when
another eligible provider is available. Circuit state is process-local, so a
multi-replica deployment isolates failures independently in each replica.

Request bodies are limited to 1 MiB by default; change the ceiling with
`MAX_REQUEST_BODY_BYTES`. Configure `TRUSTED_HOSTS` as a comma-separated list.
Development permits `*`, while production refuses to start with a wildcard or
the development API-key pepper. Production also disables `/docs`, `/redoc`, and
`/openapi.json`, and adds HSTS alongside no-sniff, frame, referrer, permissions,
and no-store response headers.

The mock provider is deterministic, costs `$0`, and requires no external API
key. It exists to validate the gateway contract without external credentials.

### Enable OpenAI

OpenAI is opt-in so a local checkout never makes a paid request accidentally.
Set these values in `.env`:

```dotenv
OPENAI_ENABLED=true
OPENAI_API_KEY=your-project-api-key
OPENAI_MODEL=gpt-5.6-terra
```

When enabled, `model: "auto"` selects OpenAI before the mock provider. Use
`model: "mock-1"` or `routing.allowed_providers: ["mock"]` to force the free
mock. Provider timeouts and retries are controlled by the gateway; SDK retries
are disabled. Requests use the Responses API with storage disabled. Current
model and pricing defaults follow the
[official OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
and remain configurable through environment variables.

Until structured tool calling is implemented, incoming `tool` messages are
sent to OpenAI as clearly labelled user text.

### Enable Anthropic

Anthropic is also opt-in. Set these values in `.env`:

```dotenv
ANTHROPIC_ENABLED=true
ANTHROPIC_API_KEY=your-anthropic-api-key
ANTHROPIC_MODEL=claude-sonnet-5-5
```

The default routing profile uses `$2` per million input tokens and `$10` per
million output tokens. All model, pricing, latency, and quality values remain
configurable through environment variables. These defaults follow Anthropic's
[model documentation](https://platform.claude.com/docs/en/models/overview) and
[pricing](https://platform.claude.com/docs/en/about-claude/pricing). The SDK's
retries are disabled so the gateway's retry, circuit-breaker, and fallback
layers remain authoritative.

System messages are combined into Anthropic's top-level system instruction,
and tool-result messages are sent as clearly labelled user text until native
tool calling is implemented. The adapter omits `temperature` because current
Claude models reject non-default sampling parameters; explicit assistant
prefills are rejected as unsupported instead of being silently changed.

### Enable a local model

The `local` provider uses the OpenAI-compatible Chat Completions API supported
by [Ollama](https://docs.ollama.com/api/openai-compatibility) and
[vLLM](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/). For
example, start an Ollama model on the host:

```bash
ollama pull qwen3:8b
ollama serve
```

When running the gateway directly on the host, set:

```dotenv
LOCAL_ENABLED=true
LOCAL_BASE_URL=http://localhost:11434/v1
LOCAL_API_KEY=ollama
LOCAL_MODEL=qwen3:8b
```

When the gateway runs in Docker Compose but Ollama runs on the host, use
`LOCAL_BASE_URL=http://host.docker.internal:11434/v1`. Compose provisions that
host mapping. Ollama requires an API-key value in the OpenAI client but ignores
it for its local server; replace `LOCAL_API_KEY` with the configured credential
when using an authenticated vLLM or other compatible endpoint.

Local token prices default to `$0` because there is no upstream per-token bill.
Set `LOCAL_INPUT_COST_PER_MILLION_USD` and
`LOCAL_OUTPUT_COST_PER_MILLION_USD` if you want routing and budgets to account
for allocated infrastructure cost. Electricity and hardware depreciation are
not estimated automatically.

With `model: "auto"`, the router classifies the request as simple, medium, or
complex, filters enabled OpenAI, Anthropic, and local candidates by provider
allowlist, capability, and `max_cost_usd`, then applies the requested
`balanced`, `cost`, `latency`, or `quality` strategy. The mock adapter is
excluded from automatic selection when a real provider is available, but
remains the deterministic development fallback and can always be selected
explicitly.

Temporary timeouts, provider rate limits, and availability failures are retried
before the gateway advances to the next ranked candidate. Configure retry
behavior with `PROVIDER_MAX_RETRIES`, `PROVIDER_RETRY_BASE_DELAY_SECONDS`, and
`PROVIDER_RETRY_MAX_DELAY_SECONDS`. Permanent request errors are returned
immediately and are never retried or routed to another provider. Successful
responses list every sanitized retry and fallback attempt in order.

Exact-match responses are cached for 300 seconds by default. Configure this
with `RESPONSE_CACHE_ENABLED` and `RESPONSE_CACHE_TTL_SECONDS`, or bypass both
cache reads and writes per request with `routing.disable_cache: true`. Cache
keys contain a hash of all output-affecting request options and are isolated by
customer. Cache failures do not block provider requests.

Configure a monthly customer budget using the client ID printed by the key
creation command:

```bash
llm-gateway-set-budget \
  --client-id <client-uuid> \
  --period 2026-10 \
  --limit-usd 25
```

The gateway atomically reserves the highest eligible estimated request cost
before making a provider call. Successful requests settle that reservation to
actual usage; failed requests release it. View the authenticated customer's
monthly totals with `GET /v1/usage?period=2026-10`. Customers without a budget
remain unlimited, and their `budget_usd` and `remaining_budget_usd` values are
`null`.

Run quality checks:

```bash
ruff check .
mypy app
pytest --cov=app
```

The same checks run automatically in the repository's
[GitHub Actions workflow](.github/workflows/ci.yml), followed by a clean Docker
image build. The workflow has read-only repository permissions and cancels
superseded runs on the same branch.

Alternatively, copy `.env.example` to `.env` and run:

```bash
docker compose up --build
```

The Compose stack starts the gateway, PostgreSQL, Redis, Prometheus, and
Grafana. The gateway waits for both required data services to become healthy,
applies Alembic migrations, and then starts FastAPI. Prometheus is available at
<http://localhost:9090>; the provisioned **LLM Gateway Overview** dashboard is
available in Grafana at <http://localhost:3000>. Anonymous access is enabled
for this local development stack only.

The dashboard displays request outcomes, P95 latency, provider errors, fallback
activity, cache hit rate, rate-limit decisions, token throughput, and cost. Its
metrics use bounded provider/model/outcome labels and never include customer
IDs, request IDs, prompts, credentials, or response content.

### Run the end-to-end smoke test

With the stack running, create a dedicated test key and pass it to the
dependency-free smoke-test script:

```bash
docker compose exec gateway llm-gateway-create-key --client-name smoke-test
python scripts/e2e_smoke.py --api-key <the-key-printed-above>
```

The script validates health, invalid-key rejection, an authenticated mock-model
completion, response caching, usage persistence, request/rate-limit headers,
and Prometheus metrics. It never invokes a paid provider. GitHub Actions runs
the same flow from a clean checkout with two customers and also verifies that
their caches, rate-limit counters, and monthly budgets remain isolated. CI
starts only the gateway, PostgreSQL, and Redis, then removes the stack and its
volumes.

To run PostgreSQL and Redis only while developing locally:

```bash
docker compose up -d postgres redis
alembic upgrade head
uvicorn app.main:app --reload
```

With the full Compose stack running, create a client key inside the gateway
container:

```bash
docker compose exec gateway llm-gateway-create-key --client-name demo
```

Use the printed client ID to set a budget:

```bash
docker compose exec gateway llm-gateway-set-budget \
  --client-id <client-uuid> --period 2026-10 --limit-usd 25
```

Readiness returns HTTP `200` only when both PostgreSQL and Redis respond; it
returns HTTP `503` with per-dependency status otherwise.

## Development stages

1. Project foundation
2. Unified LLM API
3. Provider integrations
4. Authentication and customer management
5. Rate limiting
6. Intelligent routing
7. Retries and fallback routing
8. Response caching
9. Usage budgets and cost tracking
10. Observability dashboards
11. Security and reliability hardening
12. Deployment and documentation
