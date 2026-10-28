# Task classifier dataset card

## Version and purpose

`task-taxonomy-v1` is the initial synthetic validation set for the deterministic
`task-rules-v1` classifier. It measures category selection separately from task
difficulty and exists to prevent unmeasured rule changes.

## Composition

- 30 synthetic prompts with no production or customer data.
- Equal `tune` and `heldout` splits.
- Every split covers all five task categories and all three difficulty levels.
- Tags identify multilingual, ambiguous, adversarial, and very-short cases.
- Tune examples may guide rule changes; held-out examples are used for the gate.

This is intentionally a small baseline, not a claim of broad language or domain
coverage. Later versions must add independently reviewed examples without moving
failed held-out cases into the tuning split.

## Gate

The held-out split must achieve at least `0.85` category macro F1 and no category
recall below `0.70`. Difficulty accuracy and p95 classifier latency are reported
but are not promotion gates in this first dataset version.

Run the evaluation with:

```bash
llm-gateway-classifier-eval --split heldout
```
