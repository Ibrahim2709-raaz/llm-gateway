# Intelligent Model Routing Roadmap

## Implementation status

Milestones 1 through 8 are implemented for the version 0.2 release candidate:
versioned taxonomy and evidence storage, deterministic classification,
reproducible evaluation runs, audited score and policy activation,
evidence-driven ranking, offline replay, shadow mode, operational dashboards,
alerts, deterministic customer canaries, pinned evidence versions, and an
immediate `baseline-v1` rollback path.

The code gates pass locally and in CI. Production promotion still requires the
documented shadow and canary observation windows with representative traffic.
Milestone 9 remains optional follow-on work and is not required for version 0.2.

## Product objective

Evolve the gateway from a reliable multi-provider proxy into an evidence-driven
model-selection platform. For every request, the gateway should identify the
kind and difficulty of the task, select the best eligible model for the
customer's goal, and explain the decision using versioned evaluation and live
operational data.

The target product statement is:

> Send one request. The gateway selects the best model for that job using
> measured quality, cost, latency, reliability, and customer policy.

This roadmap builds on the existing authentication, routing, provider adapters,
fallback, caching, budgets, cost tracking, PostgreSQL, Redis, Prometheus, and
Grafana foundation.

## Guiding principles

1. **Measure rather than assume.** Provider marketing claims do not determine
   model quality scores. Repeatable evaluations do.
2. **Hard policy before soft ranking.** Permissions, capability requirements,
   budget, cost ceilings, and availability always filter candidates before
   scoring.
3. **Explain every route.** A selection must be reproducible from a routing
   policy version, capability-score version, and the request's classified task.
4. **Protect customer data.** Production prompts are not added to evaluation
   datasets or retained for analytics by default.
5. **Prefer safe fallback.** Missing or low-confidence classification uses a
   documented general-purpose route rather than pretending to know the task.
6. **Separate offline evidence from live health.** Evaluation scores describe
   capability; live metrics describe latency, errors, and availability.
7. **Version and roll back.** Classifiers, datasets, scores, and routing policies
   are activated by version and can be reverted independently.

## Initial task taxonomy

The first production taxonomy is deliberately small enough to evaluate well:

| Task category | Examples | Primary quality measurement |
|---|---|---|
| Coding | Generate, explain, debug, or refactor code | Executable tests and rubric checks |
| Reasoning | Logic, mathematics, planning, and analysis | Exact answer plus reasoning rubric |
| Summarization | Condense supplied text | Faithfulness, coverage, and concision |
| Extraction | Convert supplied content into structured fields | Schema validity and field accuracy |
| General chat | Explanation, rewriting, and ordinary questions | Instruction-following rubric |

Each request also retains the existing `simple`, `medium`, or `complex`
difficulty label.

Research is not included in the first taxonomy. A model alone cannot perform
reliable research without retrieval, citations, and source-quality controls.
Research becomes a separate category after tool-capability routing exists.

## Target request flow

1. Authenticate the customer and enforce their request rate limit.
2. Validate the request and customer policy.
3. Check the customer-isolated exact-match cache.
4. Classify task category and difficulty, with confidence and classifier version.
5. Determine required capabilities, such as structured output or tool access.
6. Remove candidates that fail a hard policy or capability constraint.
7. Load the active evaluation scores and current operational statistics.
8. Estimate the maximum cost and verify the customer budget.
9. Rank candidates for the requested cost, latency, quality, or balanced strategy.
10. Execute the ordered plan with retry, circuit breaker, and fallback.
11. Record actual usage, cost, latency, outcome, and routing explanation.
12. Update metrics and return normalized response metadata.

## Milestone 0: Release the reliable gateway baseline

### Purpose

Freeze a trustworthy baseline before changing routing behavior.

### Work

- Pass the clean-checkout GitHub Actions release gate.
- Tag and document the current gateway as `v0.1.0`.
- Record the current rule-based router as policy version `baseline-v1`.
- Capture baseline test coverage, request latency, fallback behavior, and cost.
- Preserve the current router as an immediate rollback path.

### Exit criteria

- All unit, integration, container, and end-to-end checks pass.
- The release can be installed from the documented clean-checkout procedure.
- Baseline routing decisions can be replayed in tests.

## Milestone 1: Routing taxonomy and versioned data model

### Purpose

Create stable contracts for tasks, evaluations, scores, and routing decisions.

### Work

- Add enums or validated values for task category, difficulty, and capability.
- Add database entities for:
  - evaluation datasets and versions;
  - evaluation cases and expected outcomes;
  - evaluation runs;
  - per-model, per-category, per-difficulty capability scores;
  - routing policy versions;
  - routing decision snapshots.
- Store model identity with provider, exact model name, and model revision when
  the provider exposes it.
- Define score freshness and minimum sample requirements.
- Define a cold-start policy for a model with no evaluation evidence.

### Important data rules

- Evaluation cases use synthetic, licensed, or explicitly approved data.
- Production prompt and response bodies remain unpersisted by default.
- Historical routing decisions retain the score and policy versions used at the
  time; later score changes must not rewrite history.

### Exit criteria

- Migrations upgrade and downgrade cleanly.
- Referential-integrity and version-activation tests pass.
- A model without sufficient evidence follows the documented cold-start route.

## Milestone 2: Task classifier with confidence and explanations

### Purpose

Classify requests consistently without making routing dependent on an opaque
model call.

### First implementation

- Extend the current rules to produce:
  - task category;
  - difficulty;
  - confidence;
  - matched signals;
  - classifier version.
- Use request structure, requested response format, message count, prompt length,
  and carefully scoped task signals.
- Support multi-signal cases while selecting one primary category for routing.
- Route low-confidence cases to `general_chat` with conservative difficulty.

### Validation dataset

- Create a reviewed, balanced labeled set for all category/difficulty pairs.
- Keep training/tuning examples separate from held-out validation examples.
- Include ambiguous, adversarial, multilingual, and very short prompts.

### Acceptance gates

- At least 85% macro F1 on the held-out initial taxonomy dataset.
- No category has recall below an agreed minimum documented with the dataset.
- Classification adds a bounded latency measured in CI benchmarks.
- Every classification includes a safe, user-readable reason code.
- Existing explicit-model routing remains unchanged.

### Later option

Add a small classifier model only if it materially beats the rule baseline. It
must run behind a feature flag and fall back to rules on timeout or error.

## Milestone 3: Reproducible model evaluation harness

### Purpose

Replace opinion-based quality scores with repeatable evidence.

### Evaluation approach by category

- **Coding:** execute unit tests in an isolated environment; measure pass rate,
  correctness, and instruction compliance.
- **Reasoning:** prefer exact or programmatically checked answers; use a rubric
  only where deterministic checking is impossible.
- **Summarization:** measure required-fact coverage and contradiction rate;
  supplement with a versioned judge rubric.
- **Extraction:** validate JSON/schema conformance and field-level precision,
  recall, and exact match.
- **General chat:** use a fixed instruction-following and helpfulness rubric with
  blinded pairwise comparisons where appropriate.

### Harness requirements

- Invoke models through the same provider adapters used by the gateway.
- Pin dataset, prompt template, temperature, token limit, judge, and scoring code
  versions for every run.
- Separate deterministic metrics from model-judge metrics.
- Randomize model ordering and hide model identity from judge prompts.
- Store per-case results, aggregate scores, token usage, cost, latency, and errors.
- Make interrupted runs resumable and repeated runs comparable.
- Never auto-activate a score set from a partially failed run.

### Acceptance gates

- One command evaluates any enabled model against a selected dataset version.
- Re-running deterministic cases produces the same score.
- Judge-based scores report variance and sample count.
- Failed, timed-out, and refused cases are visible rather than discarded.
- A complete run creates an immutable candidate score version.

## Milestone 4: Capability registry and score activation

### Purpose

Convert evaluation results into safe, auditable routing inputs.

### Work

- Aggregate per-case evidence by model, task category, and difficulty.
- Normalize category scores to a documented zero-to-one range.
- Track sample count, confidence interval or variance, and freshness.
- Keep price and expected latency separate from quality scores.
- Add an operator review command to compare candidate scores with the active set.
- Require explicit activation of a candidate score version.
- Support instant rollback to the previous active version.

### Guardrails

- Do not rank a model for a category when its evidence is missing or stale unless
  the cold-start policy explicitly permits it.
- A model cannot claim a capability its provider adapter cannot execute.
- Evaluation score activation must not modify customer policy or provider keys.

### Exit criteria

- Operators can inspect why a score exists and trace it to evaluation cases.
- Activation and rollback are audited and tested.
- The router can read one immutable active score snapshot per decision.

## Milestone 5: Evidence-driven routing engine

### Purpose

Choose the best eligible model for the classified task and customer objective.

### Hard filters

Apply these before scoring:

- provider enabled and credential configured;
- requested model or allowed-provider restriction;
- required task and API capabilities;
- minimum evidence and score freshness;
- request maximum-cost ceiling;
- customer monthly budget;
- provider circuit state and other availability controls.

### Ranking inputs

- category-and-difficulty quality score from the active evaluation version;
- estimated request cost from prompt and maximum output tokens;
- configured and observed latency;
- recent bounded success/error rate;
- customer-selected strategy;
- deterministic tie breakers.

### Strategy behavior

- **Cost:** select the least expensive candidate that clears the minimum quality
  floor for the task.
- **Latency:** select the fastest healthy candidate that clears the quality floor.
- **Quality:** maximize task-specific evaluated quality within policy and budget.
- **Balanced:** combine normalized quality, cost, latency, and reliability with
  versioned weights. Complex tasks place more weight on quality.

### Explainability contract

Every response should include safe metadata such as:

- classified category, difficulty, and confidence;
- selected routing strategy;
- selected model and provider;
- concise reason codes;
- estimated and actual cost;
- routing policy and score versions;
- provider attempts and fallback outcomes.

Do not reveal secret policy internals, credentials, other customers' data, or
full prompts in the explanation.

### Acceptance gates

- Hard constraints have 100% deterministic test coverage.
- Offline replay produces no policy, permission, cost-ceiling, or budget breach.
- The same inputs and active versions produce the same ordered route.
- Explicit-model requests retain their documented behavior.
- Missing evaluation data safely falls back or returns a clear error.

## Milestone 6: Shadow mode and comparative validation

### Purpose

Prove the new router before it controls production requests.

### Work

- Run the intelligent router in shadow mode beside `baseline-v1`.
- Execute only the baseline route; record the candidate intelligent decision.
- Compare selection distribution, estimated cost, latency, reliability, and
  evaluation quality without doubling provider calls.
- Build a replay tool using approved synthetic or evaluation traffic.
- Review cases where the intelligent and baseline routers strongly disagree.

### Promotion gates

- Zero hard-policy violations in shadow decisions.
- Classification and routing latency remain within the documented service budget.
- No unexplained large shift toward a single provider.
- Evaluation replays show a measurable quality, cost, or latency improvement for
  the selected objective.
- Operators can explain and reproduce sampled decisions.

## Milestone 7: Dashboard and operational controls

### Purpose

Make model-selection value and failure modes visible.

### Grafana additions

- request volume by task category and difficulty;
- model selections and fallback destinations;
- estimated versus actual cost;
- cost and latency by strategy;
- cache savings;
- success rate and circuit-breaker activity;
- classifier confidence distribution;
- active policy, classifier, dataset, and score versions;
- score freshness and evaluation-run failures.

### Customer-visible usage

- Explain the selected model in response metadata.
- Show monthly tokens, estimated provider cost, budget, and remaining allowance.
- Add aggregate routing outcomes without exposing other customers or sensitive
  prompts.

### Alerts

- stale or missing capability scores;
- sudden provider latency or error increase;
- cost-estimate versus actual-cost drift;
- unexpected classification distribution change;
- evaluation regression for an active model;
- routing concentration beyond a configured threshold.

### Exit criteria

- An operator can answer why a model was selected without reading source code.
- A customer can understand the choice and cost without seeing internal secrets.
- Each alert links to a documented operator response.

## Milestone 8: Controlled production rollout

### Purpose

Introduce intelligent routing without risking all traffic at once.

### Rollout sequence

1. Local and CI evaluation fixtures.
2. Shadow mode in a non-production environment.
3. Internal customers only.
4. Small canary percentage for automatic routing.
5. Gradual increase after each observation window.
6. Default automatic routing after all release gates pass.

### Required controls

- feature flag by environment and customer;
- immediate rollback to `baseline-v1`;
- pinned policy and score versions during each canary;
- no automatic activation immediately after an evaluation run;
- documented incident and rollback procedures.

### Production-ready definition

The intelligent router is production-ready only when:

- evaluation datasets and scoring code are versioned and reproducible;
- all hard policy filters pass deterministic tests;
- task classification meets its documented validation threshold;
- shadow and canary runs show no policy breaches;
- routing decisions and score sources are explainable;
- cost, latency, reliability, and quality regressions are monitored;
- operators can activate, inspect, and roll back every routing input;
- privacy review confirms production prompts are not retained by default;
- baseline routing remains available as an emergency fallback.

## Milestone 9: Advanced routing after the first intelligent release

These are follow-on capabilities, not prerequisites for the first release:

- tool-capability routing for web research, retrieval, and code execution;
- multi-label task classification and compound-workflow routing;
- customer-specific minimum-quality policies;
- regional and data-residency constraints;
- online experiments and contextual bandits with strict safety constraints;
- human feedback and outcome-based quality signals;
- automatic benchmark scheduling when providers release a new model revision;
- semantic caching with explicit privacy and correctness controls;
- customer-facing administration and billing portal.

## Recommended implementation order

Do not skip directly to a sophisticated scoring formula. Follow this sequence:

1. Release and freeze the reliable baseline.
2. Define the taxonomy and versioned storage.
3. Build and validate classification.
4. Build reproducible evaluations.
5. Activate evidence-backed capability scores.
6. Integrate task-aware scoring into the router.
7. Validate in shadow mode.
8. Add decision and evaluation dashboards.
9. Canary and gradually release.

Each milestone should be committed, tested, documented, and demonstrated before
the next milestone changes production routing behavior.
