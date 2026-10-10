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

Steps 1 through 4 are complete: MVP planning, API contract design, the FastAPI
foundation, and PostgreSQL/Redis infrastructure. The gateway now has initial
models and migrations for clients, API keys, providers, budgets, and usage,
plus dependency-aware readiness checks.

The next milestone implements the unified OpenAI-compatible chat-completions
API and a deterministic mock provider.

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
