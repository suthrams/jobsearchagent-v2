# ADR-072: Resume Live Chat + Export in the Tailoring Flow

## Status

**Accepted** (2026-06-01). Approved via spec-driven-development Phase 1.
Implementation pending — Plan -> Tasks -> Implement next; this doc is the spec and
the decision record. The one item flagged under "Remaining open question" is
resolved at the Plan gate.

Extends ADR-068 (chat-revise loop for the Resume Clinic), ADR-055 (on-demand
tailoring), ADR-059 (human edit decision), and ADR-066 (deterministic resume
export). Same per-profile layer as ADR-062.

## Context

Two resume-improvement surfaces exist and do not connect:

- **Resume Clinic live chat (ADR-068):** an interactive, multi-turn revise loop on
  the parsed resume **with no job**. The user types feedback, `ResumeChatAgent`
  returns an updated `ResumeOverhaul` (`app/schemas/resume_clinic.py`), the
  `FidelityReviewer` runs on the rewrites every turn, a 25-turn cap +
  session-cost meter bound spend (`MAX_CHAT_TURNS_PER_CLINIC`), and the result
  exports deterministically to md / txt / html / json / docx / pdf
  (`app/services/resume_text_renderer.py`, `GET /resume-clinic/{id}/export`).
- **On-demand tailoring (ADR-055/059):** in a job-search workflow run, after jobs
  are scored, the user generates a **job-aware** `TailoredResumeDraft`
  (`app/schemas/tailored_resume_draft.py`) per job from the Workflow Detail
  tailoring card, then picks approve / revise / reject / edit. Tailoring is
  *one-shot* (the "revise" decision just re-runs generation) and has **no
  interactive chat and no export**.

The user wants the clinic's *chat-refine-inline + export* experience available on
a scored job's tailored resume: open a live chat from the tailoring card, refine
the resume inline, and export it in the same formats.

**The schema tension (why this is an ADR, not a tweak).** The clinic chat operates
on `ResumeOverhaul` (job-agnostic rewrites; `claim_type ∈
{restate,reorder,quantify,reframe}`) and the renderer composes a resume from it.
Tailoring produces `TailoredResumeDraft` (a job-aware *suggestions* shape;
`claim_type ∈ {reword,emphasize,gap,remove}`, required `impact_rationale`, strict
page-budget) with no export path. Bridging them naively would mean teaching the
chat agent the evidence-bound tailoring schema and building a second export path.

## Decision (Option A — reuse the clinic stack, seed it from the job's draft)

Add an entry point on the tailoring card that opens the **existing clinic chat +
export experience**, seeded with the resume after the job's tailoring is applied,
and anchored to the (workflow run, job). The chat agent, fidelity pairing, cost
cap, and export renderer are reused unchanged. The job's influence enters through
the **seed**, not through the chat agent — so the chat stays job-agnostic and no
schema reconciliation is required.

**Explicitly chosen over Option B (a job-aware tailoring chat):** that would evolve
`ResumeChatAgent`/prompt to the evidence-bound `TailoredResumeDraft` shape + a new
export path. Deferred; revisit only if a job-agnostic polish proves insufficient.

### The one new piece: a deterministic seed adapter

`tailored_draft_to_overhaul(draft: TailoredResumeDraft) -> ResumeOverhaul`
(new, in `app/services/`, no LLM). It converts the job's tailored bullets into
clinic `RewriteSuggestion`s so the chat *starts from the tailored state*:
- map `claim_type` (`reword→restate`, `emphasize→reframe`, ...) — the inverse of
  the existing `_CLAIM_TYPE_MAP` in `resume_clinic_runner.py`;
- carry `original_text`, `suggested_text`, `supporting_evidence`, `section_label`;
- seed **all** `reword`/`emphasize` bullets as rewrites; **drop** both `gap` and
  `remove` (gaps are never fabricated; per-bullet `remove` cannot be honored by the
  reused renderer — see Limitation — and matches current clinic behavior).
  High-`fidelity_risk` bullets are seeded as-is — every chat turn re-runs the
  Fidelity Reviewer, so a risky line is policed on the next turn (Q2, revised at
  the T1 implementation gate).
This is the mirror of the existing `build_fidelity_context_for_overhaul()` glue and
keeps evidence-binding intact (every seeded rewrite already carries evidence).

**Seed source (Q1):** the specific draft the button was clicked on — its
`edited_json` if that draft was human-edited (ADR-059), else its `tailored_json`.
A job's other drafts are untouched; each draft has its own "Open live chat".

### Reuse map (what is touched vs reused)

| Concern | Decision |
|---|---|
| Chat agent | **Reuse** `ResumeChatAgent` + `app/prompts/agents/resume_chat.txt` unchanged |
| Fidelity | **Reuse** — `FidelityReviewer` runs on rewrites every turn (ADR-068 invariant holds) |
| Cost cap | **Reuse** `MAX_CHAT_TURNS_PER_CLINIC` + session-cost meter, per session |
| Export | **Reuse** `resume_text_renderer.compose_resume` + `GET /resume-clinic/{id}/export` unchanged |
| Session/persistence | **Reuse** `resume_clinic_reviews` + `ResumeClinicRepository`, with the session **linked to the originating run + job** (new nullable columns) |
| Chat+export UI | **Extract** the clinic's chat+export panel into a shared `app/ui/components/resume_chat_panel.py`, reused by both the Resume Clinic view and the tailoring card (Q3) |
| Seed | **New** deterministic `tailored_draft_to_overhaul()` |
| Entry point | **New** "Open live chat" action on the tailoring card → opens the shared chat+export panel (Q5) |

### Data model

Add two nullable columns to `resume_clinic_reviews`: `source_workflow_run_id`
(the job-search run the chat was launched from) and `job_id`. A session with
`job_id` set is a "tailoring chat" launched from a scored job; without it, a
plain clinic session (today's behavior). Per the dual-write rule, update
`app/repositories/resume_clinic_repository.py` **and** `app/ui/db_reader.py`, and
document in `data_model.md`. (Alternative considered: a new table — rejected to
keep the chat/export code shared verbatim.)

### Flow

1. Workflow Detail → a scored job's tailoring card → **Open live chat** on a draft.
2. Backend creates a clinic session seeded via `tailored_draft_to_overhaul(draft)`,
   tagged with `source_workflow_run_id` + `job_id` (reuses the clinic cost-row
   pattern for `llm_calls`/`agent_events` attribution).
3. The user chats (existing `POST /resume-clinic/{id}/chat`): each turn runs
   `ResumeChatAgent` + `FidelityReviewer`, bounded by the turn cap + cost meter.
4. The user exports (existing `GET /resume-clinic/{id}/export?format=...`).
5. Decisions (approve / edit / reject) reuse the clinic decision path.

## Scope / non-goals

- **In:** entry point on the tailoring card; seed adapter; run/job link on the
  session; reuse of chat + fidelity + cost cap + export; the session listing under
  the job.
- **Out:** the chat seeing the JD live (Option B); any change to `ResumeChatAgent`,
  the tailoring agent, fidelity, or the export renderer; a tailored-shape export
  path; application tracking of any kind (CLAUDE.md).

## Spec operational sections

**Commands** — `uvicorn app.api.main:app --reload` (backend) ·
`streamlit run app/ui/streamlit_app.py` (UI) · `python -m pytest tests/` (suite,
mock mode) · `python -m pytest tests/ -m integration` (live).

**Project structure (where the work lands)** — `app/services/` (seed adapter +
session-from-draft helper) · `app/api/routers/` (the "open chat from tailoring"
action; chat/export endpoints reused) · `app/repositories/resume_clinic_repository.py`
+ `app/repositories/database.py` (two columns) · `app/ui/db_reader.py` (read the
link) · `app/ui/components/resume_chat_panel.py` (new shared chat+export panel,
extracted from `resume_clinic.py`) · `app/ui/components/tailoring.py` +
`app/ui/views/workflow_detail.py` (entry point) · `app/ui/views/resume_clinic.py`
(re-point to the shared panel) · `docs/architecture/` (this ADR + `data_model.md`,
`hitl.md`, `api_reference.md`).

**Code style** — follow existing patterns: agents via `BaseAgent`; out-of-graph
operation like ADR-055 (no `interrupt()`); one repository method per query shape;
deterministic services contain no LLM calls; ASCII commit messages with the
`Co-Authored-By: Claude <noreply@anthropic.com>` trailer.

**Testing strategy** — pytest, mock mode (no real LLM in CI). New invariant-style
tests: (a) seed adapter is deterministic and preserves `supporting_evidence` and
maps every `claim_type` (gap dropped, remove handled); (b) a tailoring-chat session
runs `FidelityReviewer` every turn (the ADR-068 invariant, now on the job path);
(c) the turn cap + cost meter apply to job-seeded sessions; (d) export of a
job-seeded session is byte-deterministic; (e) `db_reader` returns the new
run/job link. Full suite must stay green (currently 784).

**Boundaries**
- *Always:* run the suite before commit; run the UI smoke test after `app/ui/`
  changes; keep `FidelityReviewer` on every agent-authored turn; keep the cost cap.
- *Ask first:* the `resume_clinic_reviews` schema change (this ADR is that ask);
  any new dependency; any change to the chat/tailoring prompts.
- *Never:* let the chat fabricate (evidence-binding holds); add application-tracking
  fields; bypass fidelity on agent-authored output; exceed the chat cost cap.

## Success criteria (testable)

1. From a scored job's tailoring card, "Open live chat" opens a chat panel whose
   first state reflects the clicked tailored draft (not the untailored resume).
2. Chatting refines the resume inline, each turn fidelity-reviewed, bounded by the
   25-turn cap + a visible session-cost meter — identical UX to the clinic.
3. The session exports to all six formats via the existing renderer, deterministically.
4. The session is listed under its job/run and is per-profile isolated.
5. No change to clinic-only behavior; existing 784 tests still pass + new tests green.
6. Zero new fabrication surface (fidelity invariant provably enforced on the new path).

## Resolved decisions (Phase 1)

1. **Seed source:** the specific draft the button is on — its `edited_json` if
   human-edited, else `tailored_json`. Each draft has its own "Open live chat".
2. **Seed breadth:** all `reword`/`emphasize` seeded as rewrites; both `gap` and
   `remove` dropped (per-bullet removal isn't supported by the reused renderer —
   inherited clinic limitation; Q2 revised at the T1 implementation gate);
   high-`fidelity_risk` bullets seeded as-is (per-turn Fidelity polices).
3. **UI:** extract a shared `app/ui/components/resume_chat_panel.py`, reused by the
   Resume Clinic view and the tailoring card.
4. **Session identity:** extend `resume_clinic_reviews` with nullable
   `source_workflow_run_id` + `job_id` (no new table).
5. **Card label:** "Open live chat".

## Remaining open question (for the Plan phase)

- **Refinement hand-off:** after refining in chat, is **export the end state**
  (current spec — reuse the clinic decision path, the chat result is its own
  artifact), or should the refined result also write back into the job's
  `tailored_resumes` decision (e.g. as an `edit`)? Current spec assumes export is
  the end state; confirm or change at the Plan gate.

## Consequences

- **Positive:** the clinic's proven chat + 6-format export reach the job flow with
  one new deterministic adapter + a thin link + UI entry — minimal new surface, no
  schema reconciliation, fidelity/cost guardrails inherited.
- **Tradeoff:** the chat is a job-agnostic polish of a job-seeded draft; it does not
  reason about the JD turn-by-turn (Option B). Acceptable for v1.
- **Inherited limitation (renderer):** `resume_text_renderer` skips empty-suggested
  rewrites and only cuts whole sections, so **per-bullet removal is not applied on
  export** (already true for the clinic today). Tailored `remove` suggestions are
  therefore not seeded; a per-bullet-removal renderer capability is a possible
  future enhancement (would amend ADR-066).
- **Neutral:** small schema growth on `resume_clinic_reviews`; docs to update
  (data_model, hitl, api_reference, CLAUDE.md agent/HITL notes).
