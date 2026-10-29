# Version 3 Product Platform Roadmap

## Product objective

Turn the gateway from an API and operator toolkit into a secure, self-service
LLM management platform that a team can adopt without editing configuration
files or running administrative commands.

The target product statement is:

> Connect models once, give every team one governed API, and understand quality,
> reliability, and spend from one place.

Version 3 builds on the version 2 routing engine. It does not replace
Prometheus or Grafana: those remain the infrastructure monitoring tools. The
new web application provides customer and administrator workflows backed by
tenant-safe product APIs.

## Product principles

1. **Self-service without weaker controls.** Common customer actions move to a
   web portal, while high-risk routing and billing changes remain audited.
2. **Tenant isolation by construction.** Every product query and mutation is
   scoped to an organization; cross-organization access is tested explicitly.
3. **Secrets stay server-side.** Provider keys are encrypted and never returned
   to browsers after creation.
4. **Costs are explainable.** Provider cost estimates, customer allowances, and
   any commercial charges are shown as separate concepts.
5. **Grafana remains operational truth.** The product UI summarizes customer
   outcomes and links to technical monitoring instead of rebuilding Grafana.
6. **Safe defaults.** Prompt retention, intelligent routing, and automatic
   policy promotion remain opt-in.

## Milestone 1: Organizations, users, and roles

### Work

- Add organizations, users, memberships, invitations, and service accounts.
- Add secure browser sessions with email verification and password reset.
- Define customer owner, developer, billing viewer, and platform operator roles.
- Enforce organization scope and role permissions in the API and database.
- Record immutable audit events for key, budget, provider, policy, and rollout
  changes.
- Design an extension point for enterprise single sign-on without making it a
  version 3 launch dependency.

### Exit criteria

- Cross-organization access tests cover every customer-facing resource.
- Privileged actions require the documented role and create an audit event.
- Browser authentication and API-key authentication have separate lifecycles.

## Milestone 2: Customer web portal

The customer experience follows the approved
[portal design system](portal-design-system.md): Signal Blue actions, a warm
beige page canvas, white working surfaces, navy navigation, and accessible
semantic status colors.

### Work

- Build a responsive React or Next.js application using the FastAPI backend.
- Show monthly requests, tokens, estimated provider cost, budget, and remaining
  allowance.
- Let customers create, name, rotate, and revoke gateway API keys.
- Add routing preferences for balanced, lowest cost, lowest latency, and highest
  measured quality.
- Explain the selected provider, model, task classification, policy version,
  score version, and safe reason codes for sampled requests.
- Add a request playground with streaming output and a clear estimated-cost
  preview.
- Add budget and usage alerts without exposing prompts by default.

### Exit criteria

- A new organization can create a key, make a test request, and understand its
  usage without using a command-line tool.
- Customers see only their own organization data.
- The portal remains useful when intelligent routing is disabled.

## Milestone 3: Administrator routing control plane

### Work

- Add a model catalog with provider, model, price, context, capability, and
  availability metadata.
- Add reviewed pricing updates rather than silently changing cost configuration.
- Run and inspect evaluation jobs, compare score versions, and review failures.
- Activate or roll back score sets and policies through approval-aware workflows.
- Control shadow mode, internal allowlists, canary percentage, and evidence pins.
- Show routing concentration, classifier distribution, cost drift, evidence
  freshness, and current alert state, with links to Grafana for deep diagnosis.

### Exit criteria

- An operator can reproduce why a model was selected without database access.
- Every activation, rollback, price change, and rollout change is attributable.
- The emergency baseline rollback remains available even if the web UI fails.

## Milestone 4: Developer experience and API completeness

### Work

- Publish typed Python and TypeScript SDKs.
- Keep an OpenAI-compatible endpoint and document migration examples.
- Add server-sent event streaming with cancellation and usage settlement.
- Add structured-output and tool-capability declarations to routing constraints.
- Add idempotency keys for safely retried mutating operations.
- Add signed webhooks for budget, provider-health, evaluation, and rollout events.
- Generate API references and SDK examples from the versioned OpenAPI contract.

### Exit criteria

- A developer can migrate a basic OpenAI client by changing configuration rather
  than rewriting application logic.
- Streaming, cancellation, retries, and usage accounting have end-to-end tests.
- Webhook delivery is signed, retryable, observable, and replay-safe.

## Milestone 5: Bring-your-own-provider keys and governance

### Work

- Let organizations connect their own provider accounts using encrypted secrets.
- Use a secret manager or envelope encryption with versioned key rotation.
- Separate platform-owned and customer-owned provider credentials and costs.
- Add per-organization provider allowlists, model allowlists, quality floors,
  data-retention settings, and regional constraints.
- Add audit exports and configurable retention for metadata; keep prompt storage
  disabled by default.
- Add provider connection tests that never print or return stored secrets.

### Exit criteria

- A leaked database backup alone cannot reveal provider API keys.
- Credential rotation does not interrupt unrelated organizations.
- Policy tests prove that disallowed providers and regions cannot be selected.

## Milestone 6: Billing and financial controls

### Work

- Reconcile gateway estimates with provider usage exports or billing APIs.
- Keep provider cost, customer allowance, and customer invoice amounts separate.
- Add plans, included usage, overage rules, credits, and optional markup.
- Integrate a payment provider only after idempotent ledger and reconciliation
  behavior are tested.
- Add invoice previews, spend alerts, hard limits, refunds, and dispute audit
  records.
- Make the provider invoice the source of truth for provider charges and expose
  reconciliation differences clearly.

### Exit criteria

- Every invoice line is traceable to an immutable usage ledger entry.
- Replayed events cannot double-charge a customer.
- Billing failures cannot bypass customer budgets or corrupt provider-cost data.

## Milestone 7: Production deployment and reliability

### Work

- Add OpenTelemetry traces and correlation across gateway, database, Redis, and
  provider attempts.
- Define service-level objectives for availability, latency, and cost accuracy.
- Move long-running evaluations to durable background jobs with retries and
  cancellation.
- Provide Helm or equivalent deployment templates plus infrastructure examples.
- Add zero-downtime migration, backup restoration, disaster recovery, and
  multi-replica rollout tests.
- Add load, soak, and failure-injection tests for rate limits, fallback, budgets,
  and provider outages.

### Exit criteria

- Capacity and recovery objectives are measured rather than assumed.
- A failed deployment or provider outage has a tested, documented response.
- The platform can scale gateway traffic independently from evaluation workers.

## Recommended delivery order

1. Organizations, roles, and audit events.
2. Customer portal foundations and key management.
3. Usage, budget, routing explanations, and the playground.
4. Administrator evaluation and rollout controls.
5. SDKs, streaming, webhooks, and structured output.
6. Bring-your-own-provider keys and governance.
7. Billing and provider-cost reconciliation.
8. Production deployment, load testing, and general availability gates.

The customer portal and organization security are the version 3 minimum viable
product. Billing, enterprise identity, and advanced governance can ship in
later version 3 increments without blocking the first useful portal release.
