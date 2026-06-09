# ADR-092: Resume Clinic Chat Cost — Haiku Chat + On-Demand / At-Accept Fidelity

## Status

Accepted (2026-06-08). Supersedes ADR-091 §E (which kept per-turn fidelity for
safety). Builds on ADR-066/068 (clinic + chat-revise), ADR-091 (chat reliability +
fidelity feedback), ADR-059 (human-as-final-author), ADR-058 (model pins),
ADR-085/086 (cost discipline).

## Context

Live testing made cost the dominant concern for the Resume Clinic live chat. A
5-turn session cost ~$0.33: the Sonnet chat agent was ~84% (~$0.056/turn) and the
per-turn Haiku Fidelity Reviewer ~16% (~$0.01/turn). Two further problems surfaced:

1. The user wanted the chat *cheaper* without losing the resume quality they judge
   on this surface.
2. **Fidelity feedback never reached the final resume.** Fidelity is a *reviewer*,
   not an editor — it flags unsupported claims but does not edit the draft. ADR-091
   fed the verdict into the *next* chat turn, but nothing applied it in one step, so
   a user who chatted once, saw flags, and exported still shipped the flagged claims.

ADR-091 §E deliberately kept fidelity running every turn (reviewing the whole draft)
for the safety guarantee that the exported artifact is policed. Under the new cost
priority that per-turn review is the thing to move.

## Decision

### A. Chat agent Sonnet -> Haiku

`agents.resume_chat` moves to `claude-haiku-4-5-20251001` (~5x cheaper than Sonnet
on the dominant line item). Validated live before the swap (the schema-fidelity rule,
ADR-058 / model-eval feedback): one real chat turn on a representative profile
returned a valid `ResumeChatTurnResult`, correctly scoped `changed_sections`, kept
evidence-binding, and *fixed a seeded fidelity flag* (dropped an inflated "95%
across enterprise systems" claim). `config.example.yaml` + `tests/model_pins.json`
updated together; `config.yaml` is the local (gitignored) live config.

### B. Fidelity is on-demand + at-accept, not per chat turn

- A **chat turn no longer runs the Fidelity Reviewer.** It persists the edit and
  clears any now-stale verdict (`set_edited(..., fidelity_review=None)`).
- **On-demand check:** `POST /resume-clinic/{id}/fidelity-check` runs the reviewer on
  the current draft's full rewrite set, persists the verdict
  (`ResumeClinicRepository.set_fidelity_review`), and returns it. One Haiku call, when
  the user asks (the "Check fidelity" button).
- **At-accept gate:** the decisions endpoint runs fidelity on the accepted draft for
  `approve`/`edit`, so the verdict the user sees on the exported resume reflects its
  final state. (The chat-revise drafts that reach here are agent-authored, so
  policing them at accept is consistent with ADR-059, which exempts only hand-typed
  human wording.)

The full-draft review (ADR-091 §E's safety point) is preserved — it just runs at the
gate / on demand instead of every turn. The cost win is that intermediate turns no
longer each pay for a review.

### C. One-click "Apply fidelity fixes" — the missing editor step

The chat panel gains an **Apply fidelity fixes** button (shown when a check flagged
claims). It sends one chat turn with a fixed instruction — "for each flagged claim,
ground it (tighten wording + cite the resume fact) or remove it; never re-assert
unchanged; never fabricate" — with the prior verdict already in context (ADR-091),
then re-checks fidelity. The live preview and export then reflect the corrected
draft. This is what makes fidelity feedback actually land in the final resume, in one
click, while keeping the human in control (it's an explicit action, and grounding
only ever makes a claim smaller/truer).

### D. Model catalog currency (housekeeping)

The selectable model catalog's Opus entry moved `claude-opus-4-7` ->
`claude-opus-4-8` with corrected pricing (`$15/$75` -> the authoritative `$5/$25`).
No agent uses Opus (cost); all agent assignments were already on the latest
Sonnet 4.6 / Haiku 4.5.

## Consequences

- A 5-turn session drops from ~$0.33 toward ~$0.10: Haiku chat (~$0.02/turn) + the
  reviews the user actually triggers (check / apply / the accept gate), instead of a
  Sonnet turn + an automatic Haiku review every turn.
- Fidelity feedback reaches the export via the Apply button or by the user editing —
  the reviewer stays advisory (ADR-059), but there is now a first-class way to act on
  it.
- Trade-off vs ADR-091 §E: between edits the draft can carry un-reviewed claims (the
  verdict is cleared on edit). The accept gate and the on-demand check both review the
  full draft, so nothing reaches export unreviewed once the user checks or saves. The
  UI states "Not checked since your last edit" so this is never silent.
- A new endpoint (`/fidelity-check`) and repo method (`set_fidelity_review`) are
  added; the chat response's `fidelity_review` is now always null (kept for compat).

## PSSR

- **Performance/Cost:** the point of the change — fewer paid model calls per session;
  Haiku replaces Sonnet on the hot path.
- **Security/Privacy:** unchanged seam — the fidelity context is still built via
  `build_fidelity_context_for_overhaul` (redacted profile in the cached block,
  `raw_text` only on the sanctioned Fidelity path). The Apply instruction is a fixed
  server-known string, not user-injected.
- **Reliability:** fidelity-check and the accept gate are never-crash (a reviewer
  failure returns/persists a null verdict, never fails the request). The accept gate
  re-fetches after persisting so the response reflects the stored verdict.

## Tests

- `test_chat_does_not_run_fidelity_per_turn` — chat turn calls no reviewer; verdict cleared.
- `test_fidelity_check_runs_on_full_draft_and_persists`, `test_fidelity_check_returns_null_when_reviewer_raises`.
- `test_decision_runs_fidelity_gate_at_accept`.
- `test_chat_feeds_prior_fidelity_into_agent_context` (re-seeds the verdict via a check first).
- Cost-rollup tests updated to drop the per-turn fidelity row.
- `tests/model_pins.json` updated to Haiku for `resume_chat`.
