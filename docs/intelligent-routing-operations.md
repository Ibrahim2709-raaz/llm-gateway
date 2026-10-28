# Intelligent Routing Operations

This runbook covers shadow validation, controlled canaries, incident response,
and rollback for evidence-driven routing. The baseline router remains the safe
fallback throughout the rollout.

## Preflight checklist

Before enabling live intelligent routing:

1. Confirm the evaluation run and capability score set were reviewed.
2. Record the approved policy and score versions in the deployment change.
3. Set both required-version environment variables to those exact values.
4. Begin with an internal customer allowlist and a zero-percent canary.
5. Verify the Grafana shadow panels and Prometheus alerts have recent data.
6. Keep the previous deployment configuration ready for immediate restoration.

Example starting configuration:

```dotenv
INTELLIGENT_ROUTING_ENABLED=true
INTELLIGENT_ROUTING_SHADOW_ENABLED=false
INTELLIGENT_ROUTING_CANARY_PERCENT=0
INTELLIGENT_ROUTING_CUSTOMER_ALLOWLIST=<internal-client-uuid>
INTELLIGENT_ROUTING_REQUIRED_POLICY_VERSION=policy-v2
INTELLIGENT_ROUTING_REQUIRED_SCORE_VERSION=scores-v2
```

Increase the percentage only after an observation window shows normal errors,
latency, cost, routing concentration, and customer outcomes. Changing the
percentage keeps existing customers in a deterministic cohort.

## Emergency rollback to baseline-v1

The fastest rollback does not modify database evidence:

1. Set `INTELLIGENT_ROUTING_ENABLED=false` in the deployment configuration.
2. Restart or roll out gateway replicas using that configuration.
3. Confirm new responses report `routing_policy_version: baseline-v1`.
4. Confirm `llm_gateway_intelligent_routes_total` stops increasing.
5. Run the smoke test and verify readiness, provider errors, latency, and cost.
6. Preserve logs and the pinned policy/score versions for investigation.

Do not activate another score set as an emergency response. The required
version pins deliberately make an unexpected activation fail closed. If a
reviewed earlier evidence set must be restored, use the audited policy and score
rollback CLI commands, update both deployment pins, and repeat the preflight
checks before re-enabling live routing.

## Alert responses

### Gateway or provider failures

Check provider-attempt outcomes, circuit-open activity, credentials, quotas,
and provider status. If the increase began with a canary, disable intelligent
routing first, then compare baseline behavior. Keep it disabled if baseline
recovers or the evidence-driven route concentrated traffic on the failing model.

### Shadow evaluation is skipped

Inspect sanitized gateway logs for `intelligent_shadow_skipped`. Verify that an
active policy references an active, fresh score version and that migrations are
current. Shadow failures never change the baseline route, but they block
promotion because the candidate decision is not being evaluated.

### Shadow agreement drops

Segment the Grafana panels by task category and evidence versions. Reproduce
representative disagreements with the offline replay command. A disagreement is
not automatically wrong, but unexplained shifts block promotion.

### Routing concentration increases

Check whether the active scores, minimum-quality floors, provider availability,
or strategy weights removed alternatives. Verify that the winning provider has
capacity and that cost and latency remain acceptable. Roll back to baseline if
the concentration is unexplained or creates reliability risk.

## Prometheus rule deployment

The local Compose stack loads `monitoring/alerts.yml` automatically. Production
operators should load the same rules into their Prometheus-compatible system and
route firing alerts through their Alertmanager or managed notification service.
The repository does not configure notification destinations because those are
deployment-specific secrets and escalation policies.
