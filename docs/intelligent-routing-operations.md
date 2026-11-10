# Intelligent Routing Operations

This runbook covers shadow validation, controlled canaries, incident response,
and rollback for evidence-driven routing. The baseline router remains the safe
fallback throughout the rollout.

## Preflight checklist

Before enabling live intelligent routing:

1. Confirm the evaluation run and capability score set were reviewed.
2. Activate the approved policy and score versions through the operator portal.
3. Pin those exact versions in the persistent routing control.
4. Begin with an internal customer allowlist and a zero-percent canary.
5. Verify the Grafana shadow panels and Prometheus alerts have recent data.
6. Keep the previous deployment configuration ready for immediate restoration.

Example starting control:

```bash
llm-gateway-routing-foundation rollout-set \
  --mode live --canary-percent 0 \
  --allow-client <internal-client-uuid> \
  --policy-version policy-v2 --score-version scores-v2 \
  --actor release-operator --reason "internal canary approved"
```

Environment rollout settings remain a bootstrap fallback until the first
persistent control is saved. After that, every gateway replica reads the same
versioned database control on each request.

Increase the percentage only after an observation window shows normal errors,
latency, cost, routing concentration, and customer outcomes. Changing the
percentage keeps existing customers in a deterministic cohort.

## Emergency rollback to baseline-v1

The fastest rollback does not modify database evidence and works without the
portal:

1. Run `llm-gateway-routing-foundation emergency-baseline --actor
   <incident-commander> --reason "<incident reference>"`.
2. Confirm the command reports baseline mode and a new control version.
3. Confirm new responses report `routing_policy_version: baseline-v1`.
4. Confirm `llm_gateway_intelligent_routes_total` stops increasing.
5. Run the smoke test and verify readiness, provider errors, latency, and cost.
6. Preserve logs and the pinned policy/score versions for investigation.

Do not activate another score set as an emergency response. The required
version pins deliberately make an unexpected activation fail closed. If a
reviewed earlier evidence set must be restored, use the audited policy and score
rollback workflows, update both persistent pins, and repeat the preflight
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

### Routing evidence is expiring

Prepare and review a replacement score set before the active set expires. Do
not activate it automatically: compare it with the current set, approve the
change, update the canary's required score and policy pins, and repeat the
preflight checks. Reduce the canary or return to baseline if fresh evidence
will not be ready in time.

### Cost estimate drift increases

Compare the configured per-million input/output prices with the provider's
current price sheet and inspect provider-reported token counts. Pause canary
expansion while the estimate differs materially from actual recorded cost.
Update pricing configuration through a reviewed deployment and verify drift
returns to normal before promotion.

## Prometheus rule deployment

The local Compose stack loads `monitoring/alerts.yml` automatically. Production
operators should load the same rules into their Prometheus-compatible system and
route firing alerts through their Alertmanager or managed notification service.
The repository does not configure notification destinations because those are
deployment-specific secrets and escalation policies.
