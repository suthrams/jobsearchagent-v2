# ADR-068: Chat-revise loop for the Resume Clinic

## Status

Accepted (2026-05-29).

## Context

The Resume Clinic (ADR-066) is one-shot today: the user runs it, gets a
structured overhaul (reorganization + evidence-bound rewrites + a quality
scorecard + optional alignment), and picks one of four decisions —
`approve`, `revise`, `reject`, `edit`. The `edit` decision is supported on
the backend (the human-authored overhaul is stored in `edited_json` and
trusted as final per ADR-059), but the UI has no editor: the user can only
hit Approve, Revise, or Reject.

In practice that is not enough. The user sees the agent's overhaul, reads
it, and has specific feedback: "make the summary shorter", "promote my
projects to the top", "the experience section is too dense for one page",
"add the AWS cert to the alignment recommendation." Today the only way to
apply that feedback is to hit **Revise** and re-run the whole clinic, which
is a fresh full LLM call that may or may not address the actual concern.

What is needed is an **iterative chat loop**: type feedback in natural
language, see the resume update in place, iterate until happy. The output is
still the same evidence-bound `overhaul` shape — what changes is *who* is
driving the revisions (the user, one section at a time) instead of the
reviewer guessing what the user wants.

## Decision

A chat-revise loop scoped to the Resume Clinic. New agent, new endpoint,
new UI panel under the existing clinic results. Each turn revises the
structured overhaul; the renderer (ADR-066 export feature) shows the
result in the preview pane.

### A. New agent — `ResumeChatAgent`

- `AGENT_NAME = "resume_chat"`, Sonnet-class default
  (`claude-sonnet-4-6`), pinned in `tests/model_pins.json`. Not in
  `HIGH_VOLUME_AGENTS` (one call per user-initiated chat turn; low volume,
  quality-sensitive).
- Output schema `ResumeChatTurnResult`:
  - `reply: str` — one short conversational response (1-3 sentences) so
    the user sees the agent has understood the feedback.
  - `overhaul: ResumeClinicOverhaul` — the FULL revised overhaul
    (`reorganization` + `rewrites[]`). Same shape ADR-066 already defines
    so the renderer and the Fidelity Reviewer work unchanged.
  - `changed_sections: list[str]` — which sections the agent modified
    this turn. Audit + display, no runtime use.
- Prompt rules:
  - **Targeted section is a hint, not a license.** If the user specifies a
    section (Summary / Experience / Skills / Education / Certifications),
    rewrites for OTHER sections must be returned IDENTICAL to the input
    overhaul. The agent must not silently touch sections the user did not
    ask about.
  - Evidence binding holds. `supporting_evidence` is still required on
    every rewrite; placeholders (`[N]`, `[X]%`) survive verbatim;
    fabricated experience is flagged in `changed_sections` as a refusal
    rather than smuggled in.
  - Off-topic messages get a brief decline in `reply` and the same
    overhaul unchanged in `overhaul`.

### B. New endpoint

`POST /resume-clinic/{review_id}/chat`

Request:

```json
{
  "message":  "string — the user's free-text feedback",
  "section":  "string? — Summary | Experience | Skills | Education | Certifications | null",
  "history":  "list[{role: 'user' | 'assistant', message: str}]? — last N turns, in order"
}
```

Response:

```json
{
  "reply":              "string",
  "overhaul":           "object — the updated overhaul (reorganization + rewrites)",
  "fidelity_review":    "object | null — verdict on the updated rewrites",
  "changed_sections":   "list[string]"
}
```

Each turn:

1. Load the clinic review row + the resume.
2. Compose the agent's input context: parsed_profile (cached block) +
   current overhaul (the persisted `edited_json` if present, else the
   agent's original `overhaul`) + `section` + `history` + `message`.
3. Run `ResumeChatAgent` -> `ResumeChatTurnResult`.
4. Run the Fidelity Reviewer on the new `rewrites` (same translation
   helper as the clinic runner — ADR-066). If Fidelity flags fabrication,
   the persisted overhaul is the AGENT'S new one but the response carries
   the verdict and the UI surfaces it. The user can still revert.
5. Persist the new overhaul into `resume_clinic_reviews.edited_json`.
   The `decision` field is **not** changed by chat turns.
6. Return the reply + new overhaul + fidelity verdict.

Errors: 404 unknown review, 502 reviewer / fidelity LLM failure (mirrors
the existing clinic-runner pattern).

### C. Persistence shape

Three relevant columns on `resume_clinic_reviews` already exist:

- `overhaul_json` — the AGENT's original overhaul, retained for audit.
- `edited_json` — the human-driven revision (or the latest chat turn's
  output). Populated by chat turns AND by the explicit `edit` decision.
- `decision` — `approve | revise | reject | edit | null`.

The decision and the edited_json are decoupled: a populated `edited_json`
means "this is the user-driven state"; the `decision` field tracks
whether the user has finalized acceptance.

`compose_resume` (the renderer composer) prefers `edited_json` when it
exists, regardless of decision, EXCEPT when `decision == "reject"` (which
still falls back to the original parsed resume — reject is the explicit
"throw out the overhaul" signal). The preview banner reflects this:

| decision | edited present | banner |
|---|---|---|
| `null` | no | "Preview - no decision recorded yet" |
| `null` | yes | "Preview - editing in progress (no decision yet)" |
| `revise` | yes | "Preview - editing in progress (decision: revise)" |
| `approve` | * | (no banner — approved) |
| `edit` | yes | (no banner — final) |
| `reject` | * | (no banner — renders original resume) |

### D. UI shape (Streamlit, Resume Clinic view)

A new **"Refine with feedback"** panel sits under the existing decision
controls in the clinic view's results pane:

```
─── Live preview ───────────────────────────
[markdown render of the current state, via st.markdown(render_markdown(...))]

─── Refine with feedback ───────────────────
Section to focus on: [- / Summary / Experience / Skills / Education / Certifications]
[text area: "What would you like to change?"]
[ Send feedback ]                         [ Save final edit ]   [ Discard chat edits ]

─── Conversation ───────────────────────────
You:    "make the summary shorter and front-load the cybersecurity angle"
Agent:  "Trimmed two sentences and rewrote the opener around incident-response experience."
You:    "promote projects to the top"
Agent:  "Moved Projects above Experience and added a section-order note."
```

- Chat history lives in Streamlit session state only (NOT persisted to
  the DB in v1). The history submitted to the agent on each turn is the
  in-memory list.
- The live preview re-renders after every turn from the freshly
  persisted `edited_json`.
- **Save final edit** -> sets `decision = "edit"` via
  `POST /resume-clinic/{id}/decisions`. The chat is now the locked-in
  human-authored draft.
- **Discard chat edits** -> hits a small new endpoint
  `POST /resume-clinic/{id}/discard-edits` that nulls `edited_json` and
  clears `decision`. The preview reverts to the agent's original
  overhaul.

### E. Cost shape

One Sonnet call (the chat agent) + one Fidelity call per turn. Typical
chat session is 3-6 turns. With Sonnet at ~$3/$15 per MTok and a typical
turn at ~1.5k input + ~500 output tokens, that's ~$0.012 per turn before
caching, less with the parsed_profile cache hit. A typical session sits
around $0.05-0.15 total. Comparable to one `revise` re-run today.

The lightweight `workflow_runs` row that the original clinic created stays
the correlation id for the chat turns' `llm_calls` so per-profile cost
attribution is preserved.

## Options considered

- **Whole-overhaul rewrite per turn (chosen).** The agent returns a full
  revised overhaul shape every turn. Simpler runtime; matches the existing
  Fidelity glue and renderer. The prompt's "do not touch other sections"
  rule keeps fidelity in practice; Fidelity Reviewer catches violations.
- **Section-targeted partial result.** The agent would return only changes
  for the targeted section, and the runner would merge with the rest of
  the prior overhaul. Cheaper per turn (smaller output) but the merge
  logic is non-trivial (rewrites are keyed by `section_label` substrings,
  not section names) and the user experience suffers when the agent's
  intent ranges across sections.
- **Agent operates on the rendered markdown text directly.** Closer to a
  "WYSIWYG" feel, but loses the structured fidelity guarantees: the agent
  would have to re-derive `rewrites` from prose, and a fabricated metric
  would slip through. Rejected.
- **Persist the chat history to the DB.** Useful for resumption across
  sessions, but adds a new table and a sync seam between session state
  and persistence. Out of scope for v1; trivial to add later if the
  in-session-only behaviour proves limiting.

## Consequences

### Positive

- The clinic stops being one-shot. Users iterate on what they actually
  want changed.
- The `edit` decision becomes a real product surface, not just an API
  capability the UI can't reach.
- Cost per refinement is bounded — one Sonnet call + one Fidelity call
  per turn — and well-suited to the per-profile cost dashboard.

### Tradeoffs

- **Chat history is in-session only.** A user who closes the tab and
  comes back loses the conversation thread; only the latest persisted
  `edited_json` survives. Acceptable for v1; addressed in a future ADR
  if user feedback warrants it.
- **The agent CAN drift past the targeted section.** Prompt rules +
  Fidelity Reviewer are the safeguards. A future fast-follow could move
  to section-targeted partial results (above) if drift turns out to be a
  recurring problem.
- **One more agent to operate.** New pin entry, new prompt to version,
  one more line in the cost dashboard.

### Neutral

- The renderer (`app/services/resume_text_renderer.py`) is untouched. It
  reads `edited_json` -> `overhaul_json` -> original profile in
  decreasing preference, which the new "edited regardless of decision"
  rule simply extends.
- The Resume Clinic export endpoint (md/txt/html/json/docx/pdf) reflects
  the chat-edited state automatically — the renderer reads the same
  composer that the chat turns update.

## Non-goals

- **Not a multi-resume / multi-clinic chat.** Each chat session is
  scoped to one clinic review. To revise a different resume you start a
  new clinic and chat there.
- **Not a free-form chat assistant.** The agent's only job is to update
  the structured overhaul based on resume-revision feedback. Off-topic
  messages return the same overhaul + a brief decline in the reply.
- **Not persistent chat history.** In-session only (see Consequences).
- **Not a Fidelity-bypass.** Every persisted overhaul has been through
  Fidelity, same invariant as ADR-066. A failing Fidelity verdict
  surfaces in the UI but the user can still revert via "Discard chat
  edits."

## References

- ADR-066 — Resume Clinic (the surface this loops on).
- ADR-055 — On-demand tailoring as an out-of-graph operation (the
  pattern this follows).
- ADR-059 — Retire in-graph HITL; human-as-final-author (the rule that
  says human edits are not re-policed by Fidelity, which is why "Save
  final edit" carries the chat's last state as-is).
- ADR-067 — Preserve full resume fidelity at parse time (so the chat
  agent sees the GPA, honors, and skill groups it needs to work with).
