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

Steps 1 through 3 are complete: MVP planning, API contract design, and the
FastAPI project foundation. The next milestone adds PostgreSQL and Redis.

The first milestone will provide a runnable FastAPI service, health checks,
PostgreSQL and Redis connectivity, and an OpenAI-compatible mock completion
endpoint.

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
