# Business Rules — Job Search Agent v2

Plain-language reference for **what the system decides and why**. This is the
explainability layer: if you (or a stakeholder) ask "why did the run do that?",
the answer is here, with a pointer to the code constant or ADR that enforces it.

**No values by design.** This document carries **no numeric limits or thresholds**
— every value lives in `app/workflows/limits.py` (constants) or
`config/config.yaml` + the `user_config` table (per-profile knobs, read via
`ConfigService.get_effective_config`). Each rule names the **constant or config
key** and where it is enforced, so there is nothing here to drift. To see a
current value, open the cited source.

**Scope boundary (read first).** This system *informs a human's* career decision;
it never *takes* the decision. There is deliberately **no Apply / Save / status
tracking** — those are the human's to own (see Rule G1).

---

## 1. The pipeline (how a run flows)

A full career-review run moves a wide pool of jobs through a narrowing funnel,
paying for expensive reasoning only on jobs that survive each gate:

```
discover -> filter -> [relevance pre-filter] -> score -> auto-select
        -> deep review (critic <-> auditor) -> career advice -> [interview prep]
        -> report
```

Each stage is a gate: a job that does not pass stops there and is not paid for
downstream. The rules for each gate follow.

| Stage | What it decides | Rule refs |
|---|---|---|
| Discovery | Which postings enter the pool | D1-D7 |
| Relevance pre-filter | Drop clear mismatches before paid scoring | D5 |
| Scoring | Fit score per active track | S1-S5 |
| Auto-selection | Which scored jobs get deep review | Q1-Q4 |
| Deep review | Critique + audit of selected jobs | R1-R3 |
| Career advice | Positioning + recommendation | A1 |
| Interview prep | Coaching (on-demand by default) | I1 |
| Report | Terminal summary + metrics | persisted |

---

## 2. Discovery & filtering rules

- **D1 — Honor the run's search criteria.** Discovery uses the run's `roles` /
  `locations`; locations are one-per-line (do not comma-split "City, State");
  "Remote" triggers the remote search. (ADR-064)
- **D2 — Title/keyword filter.** Postings are kept only if their title/description
  match the configured keyword sets (`models/filters.py`).
- **D3 — Experience window (opt-in).** `search.{min,max}_years_experience` drops
  postings outside the window; `search.exclude_senior` drops senior roles. Off by
  default. (ADR-065)
- **D4 — Posting-age staleness (opt-in).** `search.max_posting_age_days` drops
  postings older than the configured age at discovery; postings with no parseable
  date are kept. Off by default. (ADR-080)
- **D5 — Relevance pre-filter (opt-in).** When `search.relevance_filter` is on, one
  cheap reasoning pass drops clear seniority/relevance mismatches
  (`too_senior` / `too_junior` / `unrelated`) **before** paid scoring. Profile-
  relative and bidirectional. **Never lose a run:** any agent failure / empty
  verdict keeps ALL jobs. (ADR-079)
- **D6 — De-duplication.** Repeat postings and already-excluded jobs are dropped
  before scoring.
- **D7 — Discovery width.** The pool is capped at `MAX_DISCOVERED_JOBS`
  (`limits.py`), configurable via `search.max_discovered` up to that ceiling; only
  relevant in manual-selection / relevance-filter modes. (ADR-060/061)

---

## 3. Scoring rules

- **S1 — Active tracks only.** A profile pursues a subset of three fixed tracks:
  `ic` -> technical, `architect` -> architecture, `management` -> leadership
  (`scoring.tracks`; default = all three). Inactive tracks are **not scored**
  (their score is `null`), do not gate selection, and are hidden in the UI.
  (ADR-071)
- **S2 — Evidence only.** Every score is based on explicit resume evidence. A
  skill not in the resume scores low — the agent never assumes it exists.
- **S3 — Required dimensions.** `overall_score` and `domain_score` are always
  produced; `overall_score` is computed only from the **active** track scores,
  never inflated above them.
- **S4 — Scored width.** The number of jobs scored is capped by `MAX_JOBS_PER_RUN`
  by default (per-run override `scoring.max_scored`, clamped to
  `MAX_SCORED_CEILING`). Read via `get_max_scored(state)`. (ADR-061)
- **S5 — Manual selection (opt-in).** When `scoring.manual_selection` is on,
  discovery casts the wide net and the run **parks** for the user to pick which
  jobs to score, instead of auto-scoring. Two phases, one workflow id. (ADR-060)

---

## 4. Qualification & selection rules

- **Q1 — Qualification is per-track, not overall.** A scored job qualifies for deep
  review if **ANY active track score meets `min_match_score`** — *not* the overall
  score. Use `qualifies_for_deep_review()` / `best_track_score()`. (ADR-071)
  - *Consequence:* a job can qualify on a single strong track score even when its
    overall score is below the threshold. Raise `min_match_score` to require
    stronger matches.
- **Q2 — Match threshold.** Defaults to `MIN_MATCH_SCORE_DEFAULT` (`limits.py`);
  override per profile via `scoring.min_match_score`. Higher = fewer jobs reach the
  expensive deep-review + advice stages = cheaper and stricter.
- **Q3 — Selection cap.** At most `MAX_SELECTED_JOBS` (`limits.py`) qualifying jobs
  are auto-selected per run, highest best-track score first. (ADR-054)
- **Q4 — Empty selection short-circuits.** If no job qualifies, `deep_review_gate`
  skips straight to the report — no critic/auditor/advisor spend.

---

## 5. Deep review rules

- **R1 — Critic then auditor, looping.** The Resume Critic critiques fit; the
  Review Auditor evaluates the critique; they loop to converge.
- **R2 — Bounded loop.** Capped at `MAX_REVIEW_ROUNDS` (`limits.py`). The loop also
  stops early once the audit clears `AUDIT_QUALITY_THRESHOLD`, or when an extra
  round improves the verdict by less than `STAGNATION_MIN_IMPROVEMENT`.
- **R3 — High-match jobs only.** Deep review runs only on the auto-selected
  qualifying jobs (Q1-Q3), never on the whole scored pool.

---

## 6. Career advice & interview prep rules

- **A1 — Advice after review.** Career advice runs once per run when there is a
  selected job, producing positioning + a recommended next action. It never
  fabricates fit.
- **I1 — Interview prep is on-demand by default.** The in-graph interview coach
  auto-fires only when `scoring.auto_interview_prep` is on (**default off**) or the
  user explicitly requested it; otherwise the user gets it via
  `POST /workflows/{wf}/jobs/{job}/interview-prep`. Read via
  `get_auto_interview_prep(state)`. (ADR-085) — *Rationale:* the top selected job
  always clears `min_match_score`, so auto-firing meant the coach ran nearly every
  run.

---

## 7. Tailoring & fidelity rules (no fabrication)

- **T1 — Every tailored claim is evidence-bound.** Each agent-authored claim must
  carry `supporting_evidence` from the original resume. (ADR-059)
- **T2 — Gaps are labeled, never invented.** Missing experience is surfaced as a
  gap — never rewritten as if present.
- **T3 — Fidelity Reviewer always runs after tailoring** (and after Resume Clinic
  rewrites). It polices the *agent*, not the human. (ADR-066)
- **T4 — A human edit is owner-authored and exempt.** A human `edit` is the final
  word and is **not** re-reviewed (the human is accountable for their own words).
  Decisions: `approve` / `revise` / `reject` / `edit`. (ADR-059)
- **T5 — Export is deterministic.** Resume export materializes a decision-aware
  draft and renders it with no LLM; unmatched rewrites are appended, never dropped.
  (ADR-066)

---

## 8. Execution limits & cost controls

Cost is a first-class rule, not an afterthought. The limit constants live in
`app/workflows/limits.py` (open it for current values):

| Constant | Governs |
|---|---|
| `MAX_JOBS_PER_RUN` | Default scored cap (override `scoring.max_scored`) |
| `MAX_SCORED_CEILING` | Hard ceiling for `scoring.max_scored` |
| `MAX_DISCOVERED_JOBS` | Wide-net discovery cap |
| `MAX_SELECTED_JOBS` | Qualifying jobs deep-reviewed per run |
| `MAX_RESEARCH_STEPS` | Research ReAct steps per job |
| `MAX_REVIEW_ROUNDS` | Critic/auditor reflection rounds |
| `MAX_LLM_CALLS_PER_JOB` | Per-job call backstop |
| `MAX_LLM_CALLS_PER_RUN` | Absolute per-run call backstop |
| `MAX_CHAT_TURNS_PER_CLINIC` | Resume Clinic chat-turn cap (ADR-068) |

- **C1 — Budget cap is observable.** When a run hits the call backstop, jobs are
  skipped and a `budget_cap_reached` security event is emitted (counts only).
  (ADR-076)
- **C2 — Cost-cap on model overrides.** A user cannot assign an expensive model to
  a high-volume agent outside the `HIGH_VOLUME_SAFE_MODELS` allowlist; the override
  is rejected and a `cost_cap_violation` event is emitted. (in code; ADR-058)
- **C3 — Per-agent model tiering.** High-volume agents run on the cheapest capable
  model; only nuanced advisory/generation agents use the premium tier. Pinned in
  `tests/model_pins.json`. (ADR-053/058)
- **C4 — Send the resume lean.** Scoring receives a projected resume
  (`project_resume_for_scoring`) that drops fields scoring never reads. (ADR-086)
- **C5 — Failed-but-billed calls count.** Spend includes billed-but-unparseable
  completions, so cost figures are complete. (ADR-077)

---

## 9. Configuration rules

- **CF1 — Two layers.** `config.yaml` (system defaults) -> `user_config`
  (per-profile overrides). No system-wide NULL layer. Read via
  `ConfigService.get_effective_config(user_id)`. (ADR-062)
- **CF2 — Profile-owned knobs (safe to change per profile):** `search.*`
  (titles, locations, filters, relevance_filter, max_discovered, age/experience),
  `scoring.*` (tracks, career_track, min_match_score, max_scored,
  manual_selection, auto_interview_prep). See `docs/architecture/config_model.md`
  for the full knob list, defaults, and ceilings.
- **CF3 — Protected knobs (system-only, never per-profile):** models/providers,
  hard execution limits, retention windows, and the cost-gating thresholds. Listed
  in `_PROTECTED_KEYS`; silently ignored if a profile tries to override them.
  (ADR-062)
- **CF4 — Restart to apply model changes.** Per-agent model/provider overrides
  take effect on restart; search/scoring knobs apply on the next run. (ADR-053)

---

## 10. Human-in-the-loop & decision ownership

- **G1 — No application tracking.** Apply / Save / status fields are intentionally
  out of scope. The system surfaces signal; the human decides and acts. *Filter
  inputs the user gives the system are in scope; outcome tracking is not.*
- **G2 — Gate the irreversible, not everything.** The workflow runs end-to-end with
  no `interrupt()`. Human approval is required only where an action is
  irreversible/outward-facing (e.g. a tailored draft the user will send). Steering
  (config, thresholds, selection) is preferred over gating. (ADR-059)
- **G3 — Human is the final author.** On tailoring, a human `edit` overrides the
  agent and is not re-reviewed (T4). The backend always validates a decision before
  persisting; the UI never auto-approves.
- **G4 — Cancellation is cooperative.** A run can be cancelled at node boundaries
  (statuses `cancelling` / `cancelled`). (ADR-083)

---

## 11. Privacy & security rules

- **P1 — Redact before the model.** Every resume profile entering an agent context
  is redacted (`redact_pii_for_llm` / `trim_resume_profile`; scoring narrows
  further via `project_resume_for_scoring`). `raw_text` reaches only the resume
  parser and the clinic Fidelity Reviewer. Enforced by an invariant test.
  (ADR-069/086)
- **P2 — At-rest state is redacted.** `state["resume_profile"]` is stored redacted;
  the un-redacted profile's only home is the `resumes` row. (ADR-070)
- **P3 — Job descriptions are untrusted input.** Instructions embedded in a posting
  are never followed; every agent prompt carries `prompts/shared/guardrails.txt`.
- **P4 — External fetches are guarded.** Custom-URL fetching blocks SSRF targets
  (loopback/internal) and records a `blocked_url_fetch` security event. (ADR-073)
- **P5 — Security events are PII-safe by construction** — counts, field names,
  reason classes, hostnames only; never resume content or identifiers. (ADR-073)

---

## See also

- `docs/architecture/workflow_model.md` — the orchestration graph (how the funnel
  is wired)
- `docs/architecture/agent_model.md` — per-agent contracts
- `docs/architecture/config_model.md` — every config knob + defaults/ceilings
- `docs/architecture/adr/ADR-000-index.md` — the decisions behind each rule
- `app/workflows/limits.py` — the limit constants (current values)
- `docs/cost_troubleshooting.md` / `docs/model_recommendations.md` — cost levers
