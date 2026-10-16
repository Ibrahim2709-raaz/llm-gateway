# LLM Gateway

A multi-provider LLM API gateway that routes requests according to cost, latency,
availability, and task complexity.

## Planned capabilities

- OpenAI-compatible chat-completions API
- OpenAI, Anthropic, and local-model providers
- API-key authentication and per-client permissions
- Redis-backed rate limiting and response caching
- Automatic retries and provider fallback
- Usage budgets and token-cost tracking
- Structured logs and request tracing
- Prometheus metrics and Grafana dashboards
- Docker-based local development environment

## Proposed stack

- Python and FastAPI
- PostgreSQL
- Redis
- Docker Compose
- Prometheus and Grafana

## Development status

Steps 1 through 8 are complete: MVP planning, API contract design, the FastAPI
foundation, PostgreSQL/Redis infrastructure, the unified chat-completions API,
the first real provider integration, API-key authentication, and distributed
request rate limiting. The gateway now supports OpenAI's Responses API
alongside its deterministic mock provider, with database-backed customer
identity and Redis-backed per-customer limits on chat requests.

The next milestone adds intelligent routing based on cost, latency, and task
complexity.

## Project documentation

- [MVP scope and acceptance criteria](docs/mvp-scope.md)
- [MVP architecture](docs/architecture.md)
- [API contract and examples](docs/api-contract.md)
- [OpenAPI 3.1 specification](docs/openapi.yaml)

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

Try the mock completion endpoint:

```bash
llm-gateway-create-key --client-name demo

curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <the-key-printed-above>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Explain an LLM gateway."}]}'
```

Run `alembic upgrade head` before creating a key. The command prints the API
key once; save it securely because the gateway stores only a visible prefix
and an HMAC-SHA256 digest. Set `GATEWAY_API_KEY_PEPPER` to a stable secret in
every environment, and never change it without rotating all API keys. The
development default is rejected when `APP_ENV=production`.

Each authenticated customer is allowed 60 requests per 60-second window by
default. Configure this with `REQUEST_RATE_LIMIT` and
`REQUEST_RATE_LIMIT_WINDOW_SECONDS`. Successful responses include limit,
remaining, and reset headers; rejected requests return `429` and `Retry-After`.
The gateway fails closed with `503 rate_limit_unavailable` if Redis cannot make
a safe rate-limit decision.

The mock provider is deterministic, costs `$0`, and requires no external API
key. It exists to validate the gateway contract before real provider adapters
are added.

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

Run quality checks:

```bash
ruff check .
mypy app
pytest --cov=app
```

Alternatively, copy `.env.example` to `.env` and run:

```bash
docker compose up --build
```

The Compose stack starts the gateway, PostgreSQL, and Redis. The gateway waits
for both dependencies to become healthy, applies Alembic migrations, and then
starts FastAPI. To run PostgreSQL and Redis only while developing locally:

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
