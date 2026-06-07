# ADR-087: Asynchronous Message Batches API scoring mode (deferred)

## Status

Proposed (deferred) (2026-06-07). Documented as a designed option; NOT
implemented. Revisit if scoring volume and cost justify an async run mode.

## Context

Scoring + research are the per-job funnel cost. Anthropic's **Message Batches
API** (https://platform.claude.com/docs/en/build-with-claude/batch-processing)
offers **50% off both input and output tokens** for requests submitted as an
asynchronous batch (up to 10,000 requests/batch, processed within 24h - usually
under an hour, often minutes).

This is distinct from in-context batching (one prompt scoring all jobs):
- **No quality risk** - each job is still one independent request, scored exactly
  as today; only the billing and the submission model change.
- **Does not reduce input tokens** (each request still carries the resume) - it
  reduces *cost* via the discount, not payload. ADR-086 handles payload.
- **Asynchronous** - results are not real-time.

## Decision (proposed, not built)

Offer an **opt-in asynchronous scoring mode** that submits the discovered job pool
to the Message Batches API instead of the synchronous concurrent fan-out, for
users who accept "submit now, results later" in exchange for ~50% lower
scoring/research cost.

Sketch (for when/if this is built):
- A new run lifecycle: `register_run` -> discover -> **submit batch** -> status
  `awaiting_batch` -> poll/webhook -> on completion, ingest results and resume the
  graph at deep review. Mirrors the two-phase manual-selection re-entry (ADR-060):
  same `workflow_id`, conditional entry point, no `interrupt()`.
- A `BatchClient` seam alongside `LLMClient` (submit / poll / retrieve); provider
  caps the batch at the API's limits; partial/expired batches fall back to the
  synchronous path (never lose the run).
- Config toggle `scoring.async_batch` (default off); applies only to scoring +
  research (the high-volume per-job Haiku calls), never to the interactive
  on-demand ops (tailoring / deep-review / interview-prep).
- UI: a "pending batch" run state with a check-back affordance; the dashboard
  surfaces batch status.

## Options considered

- **Synchronous concurrent scoring (today)** - real-time, full price. Keep as the
  default.
- **In-context batching** - rejected for scoring (truncation, lost isolation,
  attention dilution); see ADR-086 context.
- **Async Batches API (this ADR)** - biggest cost lever (50% off input AND output,
  no quality loss) but async + a non-trivial lifecycle/UI change.

## Consequences

### Positive (if built)
- ~50% off scoring + research tokens, including the expensive output half, with no
  scoring-quality change (requests stay independent).
- Scales to large discovery pools cheaply (batch of up to 10,000).

### Tradeoffs / why deferred
- **Async breaks the current run-and-watch UX** (results within 24h, no real-time
  guarantee). Needs a new async run lifecycle + polling + UI for a pending batch -
  a meaningful architecture change, not a config tweak.
- Per-job cost attribution and the per-run call-budget guardrails need rework for
  batch submission.
- The dollar case is bounded today: the tightened funnel often scores only a few
  jobs per run, and output (Sonnet advisor/critic) - not scoring - dominates cost.
  The 50% discount is most compelling at high scoring volume.
- Beta API surface; model/limit support evolves.

## References
- ADR-086 (the synchronous payload trim - shipped instead, for now)
- ADR-060 (two-phase same-thread re-entry pattern this would mirror)
- ADR-061 (on-demand ops that would stay synchronous)
- Message Batches API: https://platform.claude.com/docs/en/build-with-claude/batch-processing
