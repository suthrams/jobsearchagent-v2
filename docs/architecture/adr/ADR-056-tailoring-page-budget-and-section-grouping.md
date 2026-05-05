# ADR-056: Tailoring Page-Budget Contract and Section-Grouped Suggestions

## Status
Accepted

## Context

Real users print their resume on a fixed page count (typically two pages).
The Tailoring Agent (ADR-015) and Fidelity Reviewer (ADR-016) shipped with no
length contract. In practice this produced two failure modes that defeated the
feature for the candidate it was supposed to help:

1. **Page overflow.** Suggested rewrites were routinely longer than the bullet
   they replaced, so adopting more than a couple of suggestions reflowed the
   resume onto an extra page. The candidate then either threw away the
   suggestion or hand-edited it back to the original length, losing the
   tailoring value.
2. **No way to free space.** The agent could only reword, emphasize, or flag
   gaps. There was no first-class way to say "this bullet does not pull weight
   for this job — drop it and use the space for something stronger."
3. **Flat, hard-to-act-on output.** Suggestions came back as two flat lists
   (`summary_suggestions`, `experience_bullet_suggestions`). The candidate had
   to mentally map each suggestion back to the right section of their resume
   to apply it. With multiple experience entries this was painful enough that
   most candidates only acted on the top one or two.

The career-transition use case the tool exists for needs the candidate to be
able to apply suggestions in 5-10 minutes per job, not as a half-day editing
project. The above three problems were the dominant blockers.

## Decision

We add a page-budget contract to TailoringAgent + FidelityReviewer (prompts
bumped to v3) and a section-grouping contract to TailoredBullet:

1. **Per-bullet length budget (target = match original line count).**
   `suggested_text` word count must fall in the band
       `ceil(0.85 * original_words)` .. `floor(1.05 * original_words)`
   where `original_words` is the whitespace-token count of `original_text`.
   Word count is the proxy for line count. Overflow above 1.05x is rejected;
   collapse below 0.85x is also rejected (a one-line bullet rewritten as a
   fragment loses density and looks weak). One bullet = one sentence; no
   clause stacking, no semicolons, no parentheticals.

2. **Bullet removal as a first-class suggestion type.**
   `claim_type` gains `"remove"`. A `remove` suggestion has empty
   `suggested_text` and puts the bullet to be deleted in
   `supporting_evidence`. The agent uses this to free space for higher-value
   rewrites elsewhere on the page. Net-new bullets remain forbidden; gaps are
   still surfaced via `claim_type="gap"`.

3. **Section labels for grouped rendering.**
   Every TailoredBullet sets `section_label` using the candidate's actual
   resume section identifiers:
       - `"summary"`
       - `"experience:<company>:<title>"` (one per ExperienceEntry; company
         and title copied verbatim from `resume_profile.experience`)
       - `"skills"`
       - `"education:<institution>"` and `"certifications:<name>"` (rare)
   The Streamlit UI groups suggestions by `section_label` in resume order
   (Summary → Experience entries in order → Skills → ...) and shows a
   per-section word-delta (`24w → 19w (-5w)`) so the candidate sees the
   page-budget impact at a glance.

4. **Fidelity Reviewer enforces all three.**
   The reviewer checks (a) length-band compliance, (b) `section_label` is
   present and matches a real resume section, and (c) `remove` carries empty
   `suggested_text`. Violations land in `required_revisions` with a short
   diagnostic note (e.g. `"Bullet N: 28w > 18w original"`).

The schema change is additive and backwards-compatible:

```python
class TailoredBullet(BaseModel):
    ...
    claim_type: Literal["reword", "emphasize", "gap", "remove"]   # added "remove"
    section_label: str = ""                                        # new, default ""
    ...
```

Older drafts in `tailored_resumes` (no `section_label`, no `remove` claims)
render unchanged: the UI buckets unlabeled summary suggestions under
`"summary"` and unlabeled experience suggestions under `"experience:other"`.

## Rationale

- **Page-budget is the user's real constraint.** The whole point of tailoring
  is that the candidate can paste suggestions back into a fixed-length
  document. A suggestion that overflows is not actionable; a suggestion that
  collapses density is not desirable. The +/- 15% band recognizes both.
- **`remove` belongs in the schema, not in the agent's narrative notes.**
  Putting it in `claim_type` makes removals visible to the Fidelity Reviewer,
  countable in per-section deltas, and styleable in the UI (red badge).
  Burying "consider dropping bullet 3" in `overall_tailoring_notes` made it
  invisible.
- **Section grouping mirrors the resume.** When the candidate goes to apply
  suggestions, they work section-by-section through the document. Grouping
  the agent's output the same way removes a translation step.
- **Same fidelity contract.** Evidence-binding (ADR-015) and gap-vs-rewrite
  separation are unchanged. The reviewer's job is now broader (length and
  section validation in addition to evidence) but its verdict is the same
  approve / revise / reject shape.
- **No DB migration.** Backwards-compat defaults on the schema mean existing
  rows continue to deserialize. Prompt versions bumped from v2 to v3 so the
  observability pipeline can distinguish drafts produced under the new
  contract.

## Consequences

### Positive
- Suggestions are directly applicable: paste in the new bullet, the page
  count is preserved.
- Candidates can free space deliberately via `remove`, instead of negotiating
  with the agent or hand-editing.
- The UI renders by section and shows per-section length deltas, so the
  candidate can scan a draft and decide quickly which sections to act on.
- The Fidelity Reviewer's flag list now includes layout violations alongside
  evidence violations, surfacing the failure modes that previously went
  silent.

### Tradeoffs
- The agent has a stricter rule set, which can produce slightly more
  conservative rewrites (fewer rhetorical flourishes). This is a deliberate
  trade against page overflow.
- Older drafts have no `section_label`; the UI fallback is a single
  `"Other suggestions"` bucket per source list. Re-running tailoring produces
  a properly grouped draft.
- Length checking is word-count-based (proxy). Edge cases (very long words,
  monospace formatting) can drift from the line-count assumption. We accept
  this in exchange for a deterministic, agent-checkable rule.

### Neutral
- Per-suggestion approve / reject is **not** part of this ADR. Today the user
  approves the whole draft. A follow-up ADR will introduce per-suggestion
  decisions and iterative-revision context, building on the section-labeled
  structure this ADR establishes.

## Implementation Notes

- `app/schemas/tailored_resume_draft.py` — added `section_label: str = ""` and
  expanded `claim_type` to include `"remove"`. Docstring updated with the
  section-label format and the load-bearing-vs-tolerant field distinction.
- `app/prompts/agents/tailoring_agent.txt` — bumped to v3. Adds the
  Length Budget section, the `remove` claim type, the section_label
  requirement, and the brevity caps on narrative fields.
- `app/prompts/agents/fidelity_reviewer.txt` — bumped to v3. Adds the matching
  length-band check, the `section_label` validation, and a Length / Layout
  Patterns to Catch section that flags overflow, collapse, and missing /
  invalid section labels in `required_revisions`.
- `app/ui/streamlit_app.py` — `_render_tailored_sections()` groups bullets by
  `section_label` in resume order and shows per-section word delta;
  `_render_one_bullet()` shows length delta inline (`24w → 19w (-5w)`) and
  renders `remove` / `gap` distinctly. `_render_tailoring_card()` now takes
  `resume_profile` so the section order matches the candidate's resume.

## References
- ADR-015 — Tailoring Must Be Evidence-Bound. Unchanged; this ADR adds
  layout and structure constraints on top of the evidence requirement.
- ADR-016 — Add Fidelity Reviewer After Tailoring Agent. Unchanged; the
  reviewer's responsibilities expand to include layout enforcement.
- ADR-055 — On-Demand Tailoring as an Out-of-Graph Operation. The trigger
  surface (`POST /workflows/{wf}/jobs/{job}/tailorings`) is unchanged. Old
  drafts created before this ADR remain readable.

## Addendum 2026-05-05 — Per-suggestion rationale and strategy summary

User feedback on the v3 draft: the candidate could not tell *why* a
particular rewrite was supposed to land better with the hiring manager, and
the draft as a whole had no narrative the candidate could carry into a
cover letter or interview. Both gaps reduced the draft's usefulness even
when the suggestions themselves were sound.

The same prompt-and-schema surface is extended with two additive,
backwards-compatible fields. No new ADR; the contract spirit is identical.

- `TailoredBullet.impact_rationale: str = ""` — one short sentence
  (<= 25 words) explaining why the change strengthens the bullet for
  THIS specific job. Required to reference something concrete from the
  JD (a stated requirement, named technology, responsibility, or scope
  hint). Generic praise like "stronger phrasing" is rejected by the
  Fidelity Reviewer.
- `TailoredResumeDraft.overall_tailoring_notes` — repurposed from a
  terse note (<= 20 words, never used) to the draft's strategy summary
  (3-5 short sentences, <= 120 words): what the candidate is emphasizing
  across the draft, what is being removed and why, where the gaps sit,
  what to be ready to discuss in interview. Required for any draft with
  reword / emphasize / remove suggestions; a one-sentence summary is
  acceptable for gap-only drafts.

Both fields are meta — they do NOT go onto the resume — so the page-budget
contract (`0.85x .. 1.05x` of original word count) does not apply to them.
Length budgets here exist for the candidate's reading time.

Prompt versions bumped: tailoring_agent.txt v3 -> v4, fidelity_reviewer.txt
v3 -> v4. The Fidelity Reviewer flags missing rationale, generic rationale,
and missing strategy summary in `required_revisions`.

UI: the strategy summary is rendered as a top-of-card `st.info` callout so
the candidate reads it before scrolling through individual diffs. The
rationale is shown inline under each bullet's evidence caption with a
"Why for this role:" prefix. Old drafts (no rationale, terse old-style
notes) render unchanged: empty rationale is silently omitted; the old
short note appears in the new top callout if present.

## Addendum 2026-05-05 (#2) — Headline tailoring + impactful strategy summary

User feedback after using the v4 draft on real jobs: (1) the strategy
summary still read as generic prose despite the "3-5 sentences <=120 words"
budget; (2) the headline (the positioning tagline below the candidate's
name) was the highest-leverage real estate on the resume but was not
tailored at all. Both gaps reduced the draft's usefulness for the
career-transition use case the tool exists for.

Two changes, same contract spirit. Both backwards-compatible.

- **Headline as a first-class tailored section.**
  - `TailoredResumeDraft` gains `headline_suggestions: list[TailoredBullet]`
    (defaults to `[]` for backwards compat).
  - New section_label: `"headline"`. Section order in the UI is now
    Headline -> Summary -> Experience entries -> Skills -> ...
  - Headline length budget relaxes from `0.85x..1.05x` to "match original
    word count within +/- 3 words" because the percentage band is too
    narrow at the 5-15 word scale headlines occupy. Headlines are also
    exempt from the "one strong verb, one sentence" rule — they are
    noun-style positioning labels separated by `|` or similar.
  - The original headline may be empty (some resumes have no headline).
    In that case the agent proposes one rather than blocking on missing
    `original_text`.

- **Strategy summary structure for impact (not just length).**
  - Sentence 1 must be a positioning thesis: `"Positioning you as
    <role-shape> who <strongest hook from JD>."` Hedging, generic praise,
    or restatement of the obvious is rejected by the Fidelity Reviewer.
  - Sentences 2-4 are concrete moves, each leading with an active verb
    and naming a specific JD signal ("Quantified the latency win because
    the JD specifies SLO ownership"). Generic moves like "Improved several
    bullets to better reflect the role" are rejected.
  - Sentence 5 (optional) is a single sharp interview-prep line or the
    most important hiring-manager objection to address.
  - Reviewer flags fall under `required_revisions` with concrete diagnostics
    like `"Strategy summary opens with hedging; needs positioning thesis"`
    or `"Strategy summary lacks concrete JD-anchored moves"`.

The framing "the candidate is making a career decision based on this
summary" is now in the prompt so the model treats the field as the
load-bearing user-facing artifact it actually is, not as boilerplate.

Prompt versions bumped: tailoring_agent.txt v4 -> v5, fidelity_reviewer.txt
v4 -> v5.

## Addendum 2026-05-05 (#3) — Directional track-impact estimate (Option A)

User feedback on the v5 draft: "is it possible to estimate the score
improvement after the suggested revision?" The honest answer required
choosing between three options:

  - A. Cheap directional estimate from suggestion structure. No extra LLM
       call. Tells the candidate WHICH tracks the draft is moving toward,
       not what number the agent would assign.
  - B. Apply suggestions to a synthesized resume_profile and re-call
       ScoringAgent. Concrete number, but the same agent now scores text
       written specifically toward its rubric — partly real lift, partly
       tautology, plus run-to-run variance the candidate would read as
       precision.
  - C. B but with confidence intervals from a re-scored baseline. Most
       honest, but most work.

We chose A. The reasoning: the candidate's actual question is "is this
draft directionally better, and where," and A answers exactly that without
inventing precision the system does not have. Option B's self-fulfilling
prophecy is a real failure mode — promising a "78 -> 84" lift that the
ScoringAgent then "confirms" because we wrote toward its keywords would be
worse than not estimating at all.

Implementation lives entirely in the UI layer (no schema, no DB, no extra
prompt budget). For each reword/emphasize bullet across headline + summary
+ experience the heuristic:

  1. Tokenizes original_text and suggested_text (lowercased, hyphens kept
     so "multi-region" survives).
  2. Computes the set difference (tokens added in suggested vs original).
  3. Intersects the added tokens with curated keyword buckets per track:
     technical (kubernetes, postgres, prometheus, ...), architecture
     (scaled, p99, multi-region, ...), leadership (led, mentored,
     cross-functional, ...).
  4. Aggregates per track, mapping the count to a signal:
       - 0 added       -> "neutral"
       - 1-2 added in <= 1 bullet  -> "small lift"
       - otherwise     -> "likely lift"
  5. Counts claim_type="remove" bullets as "freed N bullets of space" and
     claim_type="gap" bullets as "N gaps remain unclosed" — both shown as
     a footer.

The keyword sets are deliberately narrow and ASCII; generic verbs like
"delivered" only contribute when they fall in the leadership set. The UI
caption explicitly tells the candidate this is a heuristic about direction,
not a re-score.

Pros: deterministic, zero cost, transparent (tokens shown), no latency added
to draft generation.
Cons: misses semantic lift that does not surface as added keyword tokens
(e.g. an emphasis rewrite of an existing K8s bullet). For the career-
transition use case this is acceptable — the candidate is using this to
decide WHICH suggestions to apply, and "which tracks improve" is the
right granularity for that decision.

Implementation: `_estimate_track_impact()` and `_render_estimated_impact()`
in `app/ui/streamlit_app.py`. Rendered in the tailoring card between the
Strategy summary and the section diffs.
