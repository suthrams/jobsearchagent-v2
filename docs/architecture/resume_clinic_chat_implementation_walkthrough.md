# Resume Clinic chat-revise — Implementation Walkthrough

Companion to [ADR-068](adr/ADR-068-chat-revise-loop-for-the-resume-clinic.md).
Where the ADR locks the **decision** (chat-revise loop on the existing clinic
overhaul, agent-driven, in-session history), this doc names the **files,
signatures, and tests** that will land in the build, plus any deviations from
the ADR worth flagging up front.

Read this before approving code.

## Build conventions (carry over from ADR-066's walkthrough)

- **Pacing:** one focused commit for this feature. The scope is small enough
  (one agent, one endpoint, one UI panel, one composer tweak) that the
  phase-by-phase pacing of ADR-066 is overkill.
- **Tests in CI = unit only.** Live LLM verification happens in the existing
  Resume Clinic E2E notebook by hand; no new live-test scaffolding.
- **Pin discipline.** `resume_chat` is added to `tests/model_pins.json` so
  the build-time invariant from commit `e31cee1` covers it.
- **Prompt rule.** The new prompt is loaded through `PromptLoader` (guardrails
  auto-injected). The agent receives the parsed resume profile, never raw
  resume text.
- **Decision model (ADR-059).** Chat turns populate `edited_json` only.
  `decision` is changed exclusively by the explicit user actions
  (**Save final edit** → `decision = "edit"`, **Discard chat edits** →
  `edited_json = null` AND `decision = null`).

## 1. Schemas

**New** — `app/schemas/resume_chat.py`:

```python
class ResumeChatTurnResult(BaseModel):
    reply: str = Field(min_length=1, max_length=400)
    # Same shape as the Resume Clinic overhaul. Re-imported, not redefined,
    # so renderer + Fidelity Reviewer translation work unchanged.
    overhaul: ResumeOverhaul     # {reorganization, rewrites}
    changed_sections: list[Literal[
        "summary", "experience", "skills", "education",
        "certifications", "reorganization",
    ]] = Field(default_factory=list)
```

**Used by the endpoint request body** (defined inline in the router, not a
shared schema — keeps the schemas/ folder for agent outputs only):

```python
class ResumeChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    section: Literal[
        "summary", "experience", "skills", "education",
        "certifications", "whole",
    ] = "whole"
    history: list[dict] = Field(default_factory=list)  # [{role, message}]
```

Tests: schema validation (rejects empty message, rejects message > 2k,
rejects unknown section, accepts valid history shape).

## 2. Agent

**New** — `app/agents/resume_chat.py`:

```python
class ResumeChatAgent(BaseAgent):
    AGENT_NAME = "resume_chat"

    def run(self, workflow_id: str, context: dict) -> ResumeChatTurnResult:
        result = self._run(workflow_id, context, ResumeChatTurnResult)
        return ResumeChatTurnResult(**result)
```

**New** — `app/prompts/agents/resume_chat.txt` (versioned `# version: 1`):

- Role: revise the structured `overhaul` based on the user's feedback,
  keeping evidence-binding intact.
- Input shape: parsed_profile (in `_cached` block), current overhaul,
  history (last N turns), section focus (`whole` by default), user message.
- Output shape: `ResumeChatTurnResult`.
- Hard rules:
  - Targeted section is a **hint**: rewrites for OTHER sections must
    be returned IDENTICAL to the input overhaul.
  - Every rewrite must carry `supporting_evidence` (`min_length=1`).
    Placeholders survive verbatim.
  - Off-topic messages: brief decline in `reply`, return the same
    overhaul unchanged in `overhaul`, `changed_sections = []`.

**Modified** — `config/config.example.yaml` and (local) `config/config.yaml`:

```yaml
agents:
  ...
  resume_chat:  {provider: claude, model: claude-sonnet-4-6}
```

**Modified** — `tests/model_pins.json`:

```json
"resume_chat": {"provider": "claude", "model": "claude-sonnet-4-6", "validated_on": "2026-05-29"}
```

Tests:
- `test_run_returns_chat_turn_result_instance`
- `test_run_calls_provider_with_correct_agent_name_and_schema`
- `test_passes_parsed_profile_not_raw_text`
- `test_emits_started_and_completed_events`
- `test_propagates_llm_provider_error`
- `test_resume_chat_pinned_in_model_pins_json`

## 3. Composer change (`app/services/resume_text_renderer.py`)

The renderer composer (`compose_resume`) is extended so the preview reflects
the chat state as it accumulates — not just after a final `edit` decision.

```python
def compose_resume(profile, overhaul, edited, decision):
    d = (decision or "").strip().lower() or None

    # NEW rule: prefer edited whenever it exists, EXCEPT on reject
    # (reject is the explicit "throw out the overhaul" signal).
    if d == "reject":
        applied = None
    elif edited:
        applied = edited
        if d == "edit":
            banner = None
        elif d in ("revise", None):
            banner = f"Preview - editing in progress (decision: {d or 'none'})."
        else:
            banner = f"Preview - editing in progress (decision: {d})."
    elif d == "approve":
        applied = overhaul
        banner = None
    elif d == "revise":
        applied = overhaul
        banner = "Preview - decision is 'revise'."
    elif d is None:
        applied = overhaul
        banner = "Preview - no decision recorded yet." if applied else None
    else:
        applied = overhaul
        banner = f"Preview - unrecognized decision: {decision!r}." if applied else None
    ...
```

The existing renderer tests (`test_compose_*`) still pass because none of
them populate `edited` while leaving `decision` outside of `{edit, reject}`.
New tests cover the chat-edit-in-progress combinations.

Tests:
- `test_compose_edited_overrides_overhaul_even_when_decision_is_null`
- `test_compose_edited_overrides_overhaul_when_decision_is_revise`
- `test_compose_edited_overrides_overhaul_when_decision_is_approve` (this
  is a corner case — approve was already the "use overhaul" branch; now
  the explicit `edited` always wins, except on reject)
- `test_compose_reject_still_renders_original_even_with_edited`

## 4. Runner — extract the Fidelity translation helper

`app/services/resume_clinic_runner.py` already has the
`_translate_clinic_to_tailoring_shape` helper that packs clinic rewrites into
the `TailoredResumeDraft` envelope the Fidelity Reviewer prompt expects.
Promote it from a private to a module-level function the chat endpoint can
reuse:

```python
# Renamed and made public
def build_fidelity_context_for_overhaul(
    *, profile: dict, overhaul: dict,
    review_id: str,
) -> dict:
    ...
```

Both the original clinic runner and the new chat endpoint call this helper.
No behaviour change for the clinic runner.

Tests: existing `test_resume_clinic_runner.py` tests cover this path; no
new tests beyond confirming the helper is callable from the new module
location.

## 5. Endpoint

**New** in `app/api/routers/resume_clinic.py`:

```python
@router.post("/resume-clinic/{review_id}/chat", response_model=ResumeChatResponse)
def chat_resume_clinic(
    review_id: str,
    body: ResumeChatRequest,
    deps: WorkflowDependencies = Depends(get_deps),
) -> ResumeChatResponse:
    ...

@router.post("/resume-clinic/{review_id}/discard-edits", status_code=200)
def discard_resume_clinic_edits(
    review_id: str,
    deps: WorkflowDependencies = Depends(get_deps),
) -> dict:
    ...
```

`chat_resume_clinic` flow:

1. Load the clinic review row + the resume.
2. Use `row["edited"] or row["overhaul"]` as the current overhaul.
3. Build the agent context: `_cached: {resume_profile, current_overhaul,
   target_section, history}` + `user_message`.
4. Call `ResumeChatAgent` (LLMProviderError → 502).
5. Call the Fidelity Reviewer on the new `rewrites` via the shared helper
   (LLMProviderError → log + persist with `fidelity_review=null`).
6. Persist the new overhaul into `edited_json` via a new
   `ResumeClinicRepository.set_edited` method (decision unchanged).
7. Return `{reply, overhaul, fidelity_review, changed_sections}`.

> **ADR-091 update.** The step-3 context now also carries `prior_fidelity` (the
> previous turn's compact verdict: `{status, recommendation, unsupported_claims}`)
> so the agent self-corrects flagged claims instead of re-asserting them - this is
> the convergence/cost lever. The step-5 review runs on the FULL rewrite set every
> turn (never narrowed to the changed bullets), so a claim flagged earlier stays
> policed and the persisted verdict describes the whole draft the user exports. The
> shared chat panel refreshes `rc_last_review` from the chat **response** (not
> `list_resume_clinic_runs`, which excludes job-anchored ADR-072 sessions and so
> silently froze the job-focused preview), and `ResumeClinicRepository.set_decision`
> only overwrites `edited_json` when an explicit payload is supplied - a payload-less
> decision (e.g. `approve` after chatting) no longer clobbers the accumulated edits.

`discard_resume_clinic_edits` flow:

1. Load the clinic review row.
2. `ResumeClinicRepository.discard_edits(review_id)` clears `edited_json`,
   `decision`, and `decided_at`.
3. Return `{cleared: true}`.

**New** in `app/repositories/resume_clinic_repository.py`:

```python
def set_edited(self, clinic_id: str, edited: dict,
               fidelity_review: dict | None = None) -> None:
    """Replace the chat-driven edited_json + fidelity. decision unchanged."""

def discard_edits(self, clinic_id: str) -> None:
    """Null out edited_json, decision, decided_at - revert to agent overhaul."""
```

**New response model** in `app/api/schemas/responses.py`:

```python
class ResumeChatResponse(BaseModel):
    reply: str
    overhaul: dict | None
    fidelity_review: dict | None
    changed_sections: list[str]
```

Tests (`tests/v2/test_resume_clinic_router.py`):
- `test_chat_round_trip_persists_edited_and_returns_reply`
- `test_chat_always_runs_fidelity_on_rewrites`
- `test_chat_persists_null_fidelity_when_reviewer_raises`
- `test_chat_404_when_review_unknown`
- `test_chat_422_when_message_empty`
- `test_chat_422_when_unknown_section`
- `test_chat_does_not_change_decision_field`
- `test_discard_edits_clears_edited_and_decision`
- `test_discard_edits_404_when_review_unknown`

## 6. API client

**New** in `app/ui/api_client.py`:

```python
def chat_resume_clinic(review_id: str, message: str, *,
                       section: str = "whole",
                       history: list[dict] | None = None) -> dict:
    ...

def discard_resume_clinic_edits(review_id: str) -> dict:
    ...
```

## 7. UI

**Modified** — `app/ui/streamlit_app.py`, Resume Clinic view:

Under the existing "Decision" controls and ABOVE the "Export the final
resume" panel, add a new **"Refine with feedback"** block:

```
─── Live preview ───────────────────────────
(st.markdown of render_markdown over the composer's current state -
 updates after every chat turn, since the preview reads edited_json)

─── Refine with feedback ───────────────────
[ Section: Whole resume | Summary | Experience | Skills | Education | Certifications ]
[ st.text_area("What would you like to change?") ]
[ Send feedback ]                [ Save final edit ]   [ Discard chat edits ]

─── Conversation ───────────────────────────
You:    "<message>"
Agent:  "<reply>  ·  changed: experience"
You:    "<message>"
Agent:  "<reply>"
```

Session state:
- `rc_chat_history: list[dict]` — `[{role: "user"|"assistant", message: str}]`
- `rc_last_review` is updated after every turn from the API response.

Buttons:
- **Send feedback** → calls `api.chat_resume_clinic(...)` with the in-session
  history, appends both messages to history, persists the response into
  `rc_last_review` so the preview re-renders.
- **Save final edit** → calls the existing
  `api.submit_resume_clinic_decision(clinic_id, "edit", edited=...)`. The
  payload `edited` is the current `edited_json` (which the chat turns
  populated). Sets `decision = "edit"`, locks the state.
- **Discard chat edits** → calls
  `api.discard_resume_clinic_edits(clinic_id)`, clears
  `rc_chat_history`, refetches the clinic row.

Manual verification only (Streamlit views are not unit-tested in this repo).
I will not commit the feature claiming it works without you opening it in a
browser and trying at least one round-trip.

## 8. CHANGELOG entry

Standard shape under `2026-05-29` ahead of the existing entries. Documents
the new agent + endpoint + UI panel, the cost shape, the in-session history
limitation, and the "Save final edit" decision-shape.

## Deviations from ADR-068

| Deviation | Why |
|---|---|
| `section` in the request body uses `"whole"` instead of `null` for "no focus" | Avoids `Optional[Literal]` parsing complications in FastAPI; keeps the agent prompt branching simple |
| `ResumeChatRequest.history` is `list[dict]` rather than a typed Pydantic shape | History items are short, validated by length and role-enum at the prompt level; over-typing here adds friction without value |
| Fidelity LLM failure → persist with `fidelity_review = null` (does NOT fail the turn) | Matches the existing `resume_clinic_runner.py` behaviour; the user still gets the edit, the verdict is shown as "unavailable" |
| Agent always returns the FULL overhaul, even when targeting a section | Simpler runner + Fidelity glue. The agent's prompt rule keeps non-targeted sections unchanged; Fidelity catches drift |
| Composer's "edited wins regardless of decision" rule applies to `approve` too | The user explicitly populated `edited`; rendering the agent's overhaul instead would be surprising. Reject is the only exception |
| No new schema column on `resume_clinic_reviews` | The existing `edited_json` + `decision` columns carry all the state. Chat history lives only in session |

## Open question before code lands

1. **Section enum**: should "alignment" be in the focus list? Today the
   alignment block is computed but not part of `overhaul`; the chat agent
   doesn't currently touch it. Plan defers — alignment is the JD-fit read,
   not a resume-content section.

If anything in the above feels off, name the item and I'll revise the doc
before any code lands. Otherwise approve and I'll build straight through
this doc.
