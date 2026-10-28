# Deployment Guide

This guide describes a small production deployment of the LLM Gateway. The
repository's default Compose stack is intended for local development and CI;
it exposes data and monitoring ports and enables anonymous Grafana access, so
it must not be deployed unchanged on a public host.

## Recommended topology

- Run the gateway behind a TLS-terminating reverse proxy or managed load
  balancer.
- Use managed or separately operated PostgreSQL and Redis services that are not
  exposed to the public internet.
- Store provider keys, database credentials, and the gateway API-key pepper in
  a secret manager rather than an image, repository, or Compose file.
- Scrape `/metrics` from a private Prometheus network and require authentication
  for Grafana.
- Send container stdout to a centralized JSON log service.

The gateway is stateless except for PostgreSQL and Redis. Multiple replicas can
share those services. Rate limits and response caches are distributed through
Redis; provider circuit-breaker state is intentionally local to each gateway
process.

## Required production configuration

Set at least these environment variables:

```dotenv
APP_ENV=production
DATABASE_URL=postgresql+asyncpg://gateway:<password>@<database-host>:5432/gateway
REDIS_URL=redis://:<password>@<redis-host>:6379/0
GATEWAY_API_KEY_PEPPER=<long-random-secret>
TRUSTED_HOSTS=api.example.com
```

Enable only the provider adapters that are meant to receive traffic. For
example, set `OPENAI_ENABLED=true` and `OPENAI_API_KEY`, or
`ANTHROPIC_ENABLED=true` and `ANTHROPIC_API_KEY`. Keep provider credentials in
the deployment secret store. Production startup rejects the development pepper
and wildcard trusted hosts.

The API-key pepper is used to authenticate every gateway-issued credential.
Changing it immediately invalidates all existing keys, so rotate it only with
a coordinated API-key rotation plan.

## Build and release

Build an immutable image from a reviewed commit and tag it with that commit:

```bash
docker build --pull -t registry.example.com/llm-gateway:<git-sha> .
docker push registry.example.com/llm-gateway:<git-sha>
```

Before starting the new application version, run the database migration once
as a release job using the same image and production secrets:

```bash
docker run --rm \
  --env-file /secure/path/gateway.env \
  registry.example.com/llm-gateway:<git-sha> \
  alembic upgrade head
```

Then start the gateway image with its default command. Do not run migrations
independently in every replica. Size `DATABASE_POOL_SIZE` and
`DATABASE_MAX_OVERFLOW` so their combined value multiplied by the replica count
stays below the PostgreSQL connection limit.

## Health, traffic, and shutdown

- `/health/live` confirms that the process is running.
- `/health/ready` returns `200` only when PostgreSQL and Redis respond.
- Route traffic only to ready replicas.
- Allow sufficient termination grace for active provider requests; provider
  timeouts can be as high as the configured adapter timeout.
- Keep `/docs`, `/redoc`, and `/openapi.json` disabled in production, as the
  application does automatically when `APP_ENV=production`.

After deployment, issue a dedicated low-privilege smoke-test API key and run:

```bash
python scripts/e2e_smoke.py \
  --base-url https://api.example.com \
  --api-key "$GATEWAY_API_KEY"
```

The test uses only the deterministic `mock-1` model. It validates liveness,
readiness, invalid-key rejection, a real authenticated request, response-cache
behavior, persisted usage, rate-limit headers, request IDs, and Prometheus
metrics without making a paid provider call.

## Monitoring and alerts

Import the provisioned Grafana dashboard or use its PromQL queries as a
starting point. Alert on at least:

- readiness failures and restart loops;
- elevated gateway or provider error rates;
- provider latency and timeout increases;
- sustained fallback or circuit-open activity;
- rate-limit backend errors;
- unusual token or cost growth;
- PostgreSQL connection saturation and Redis memory pressure.

Metrics deliberately omit customer IDs, prompts, credentials, and generated
content. Request IDs are the join key between client reports and structured
logs.

The Compose stack loads the routing alert rules in `monitoring/alerts.yml`.
Prometheus displays their state locally; production deployments must connect
them to Alertmanager or the managed monitoring notification system. Follow the
[intelligent-routing operations runbook](intelligent-routing-operations.md) for
canary promotion, incident response, and immediate rollback to `baseline-v1`.

## Backup and rollback

- Back up PostgreSQL before every migration and test restoration regularly.
- Configure Redis persistence only if preserving cache/rate-limit state across
  restarts is operationally valuable; PostgreSQL remains the durable usage and
  budget store.
- Prefer forward-compatible migrations so the previous application image can
  run during a rolling deployment.
- To roll back application code, redeploy the previous immutable image. Do not
  downgrade the database unless the migration was explicitly reviewed as safe
  and a verified backup is available.
- Re-run the smoke test after rollback.

## Release checklist

1. CI lint, typing, unit/integration tests, image build, and full-stack smoke
   test are green.
2. The release image is tagged with the reviewed commit and pushed to the
   registry.
3. Production secrets and trusted hosts are configured.
4. A current PostgreSQL backup has been verified.
5. Migrations complete successfully as a one-off release job.
6. Readiness is healthy before traffic shifts.
7. The post-deployment smoke test passes.
8. Dashboards and alerts show normal traffic, latency, errors, and cost.
