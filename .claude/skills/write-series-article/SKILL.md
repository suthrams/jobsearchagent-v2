---
name: write-series-article
description: >-
  Drive the v2 LinkedIn article-series process end to end: frame -> ground ->
  draft -> diagrams -> verify -> promo post -> publish handoff. Use when writing,
  drafting, revising, or reviewing an article in the "operating a real agentic
  system" series (blogs/blog_v2_articleN.md), or producing its LinkedIn promo
  post. Enforces the two hard gates (lock the framing before any prose; pass the
  verification + substance + editorial review before publish) and the
  never-overwrite-a-published-image rule.
---

# Write a v2 series article

This skill orchestrates the *process*. The canonical rules live in
`blogs/blog_v2_series_guidelines.md` (816 lines, the source of truth) and the
per-article topics in `blogs/blog_v2_series_plan.md`. This file says WHEN to read
what, WHICH gate blocks the next step, and HOW to sequence the work. When a step
cites "Section N," that is a section of the guidelines doc; open it and follow it
rather than relying on this summary.

Both docs are under the gitignored `blogs/` folder. If either is missing, stop and
tell the author. Do not reconstruct the rules from memory.

Two rules override everything below:
- **Frame before you draft.** No prose until the framing Q&A is locked (Phase 1).
- **Never overwrite a published image.** Per-action confirmation before writing
  any existing `blog_images/*.png`. A blanket OK does not carry (Section 7,
  "Asset safety"; `feedback_confirm_before_overwrite.md`).

---

## Phase 0 - Orient (read first, every time)

1. Read `blogs/blog_v2_series_guidelines.md` in full. It is updated as the series
   learns; do not assume the version in your memory is current.
2. Read the article's row in `blogs/blog_v2_series_plan.md` (topic, hook, planned
   diagrams, series position).
3. Read the **most recent published article** (`blogs/blog_v2_article{N-1}.md`) to
   match rhythm and to get the prior title + URL for the subtitle chain.
4. Recall the relevant memory (the index `MEMORY.md`): the per-article framing /
   published memos, the voice and structure feedback, and `reference_image_pipeline`.

Confirm the series identity: from Article 6 on, every piece reads as an operator
lesson learned after building, not a tutorial. It must advance the North Star
(Section 1). If the topic is on the "do not write into" saturation list
(Section 15), or describes something not yet built (the write-only-what-you-built
rule, Section 16), stop and raise it before going further.

## Phase 1 - Frame (HARD GATE: no prose before this is locked)

Per `feedback_plan_before_drafting.md` and Section 14, walk the seven-question Q&A
WITH the author. Do not draft, do not iterate prose, until the answers are locked:

1. Audience - who specifically reads this?
2. Series thesis - which argument does it reinforce?
3. Per-article job - what does the reader earn?
4. Length budget - tight / balanced / substantial (Section 6)?
5. Rendering surface - diagram production path (default: deterministic renderer)?
6. Diagram set - which specific figures, each with a narrative job (Section 7)?
7. Hook framing - personal vs engineering tone calibration?

For an article whose fundamentals are already in the series plan, this collapses to:
confirm the locked plan decisions + settle the 2-3 article-specific calibrations.

Lock the title direction too (Section 8): stakes not definitions, the article's own
quotable lesson line, numbers where data supports them. A curiosity-hook H1 paired
with an internal thesis line is allowed (Article 12 lesson).

Use AskUserQuestion for the calibration choices the author must make; do not guess
them. Output the locked framing as a short brief and get explicit sign-off before
Phase 2.

## Phase 2 - Ground in the real build

The series' entire credibility is real code and real numbers (Sections 19, 20).

- For any subsystem article, build a **code-grounded spine table** before drafting
  (boundary/table/contract -> why), verified against the actual interfaces. It
  gives the piece structure and keeps every claim accurate (Article 12 lesson).
- Pull every number from the live database (`data/v2.db`), re-derived for this
  article, in ONE pass, and reconcile across the piece (Article 11 lesson). State
  our numbers as ours; attribute any external figure inline (Section 19). Honest
  zeros count.
- Reference Articles 3-4 for the architecture overview instead of re-explaining the
  whole system.

## Phase 3 - Draft

Follow the structure top-to-bottom (Section 5): headline, top one-liner disclosure,
banner, subtitle chain, hook, reader-context paragraph (required from Article 5 on),
diagram-anchored body sections, closing through-line, "Where to go in the repo,"
standard CTA (Section 10, verbatim), further reading (Section 11), hashtags
(Section 12).

Voice is non-negotiable (Section 3): US English; NO em dashes anywhere; ASCII only;
hand-written voice; first person earns its place but do not wall-to-wall "I"; avoid
AI tells. Naming: "the script" / "the system," never v1/v2 in published text
(Section 4).

Diagram-as-spine (Section 7): lean prose, detail in rich alt-text + one bolded
takeaway per section. If a section reads as flowing essay paragraphs between images,
it is too heavy - move detail into the diagram. Code snippets are illustrative,
3-5 short ones, language-labeled (Section 9).

Aim low on word count and flex up only when content earns it (Section 6).

## Phase 4 - Diagrams and banner

Default path is the **deterministic renderer** (`tools/render_figures.py`, JSON
specs in `tools/figure_renderer/specs/`): exact text, ASCII, no fabrication risk,
Claude can drive it end to end (Section 7, `reference_image_pipeline`). ChatGPT
image-gen is retained ONLY for the banner and genuinely organic visuals.

- **HARD RULE - never overwrite a published image.** Top-level
  `blog_images/diag_*.png` are published assets and are NEVER a render target. D2
  blueprints render to `blog_images/_blueprints/`. Confirm per-action before writing
  any existing PNG. `blogs/` is gitignored - there is no git recovery.
- Legibility rules that most often get violated (Section 7): center 60-70% safe
  zone, never hide the lesson in gray text, 3-second comprehension, change styling
  never wording on any regeneration, no AI clip-art, real arrows with off-line
  labels.
- Tables ship as diagram images, not markdown (LinkedIn does not draw md tables).
- Banner: thesis not architecture, 16:9 at 1920x1080, text inside center ~80%.
- Local-preview gotchas: no `[ ]` in alt-text; vault-root-relative image paths
  (`blogs/blog_images/...`).
- Batch render-and-view; never one-render-one-view-loop (token economy,
  `feedback_token_economy_tooling`).

## Phase 5 - Verify (HARD GATE: all three before publish)

Form, then truth, then landing. All three are required (Sections 13, 22).

1. **Mechanical gate (Section 13).** Run the Python snippet from repo root against
   `blogs/blog_v2_articleN.md`. Pass: em dashes 0, en dashes 0 (ranges OK),
   non-ASCII 0, british none, word count in band. Also run the Obsidian alt-text
   check: `grep -nE '!\[[^]]*\[' blogs/blog_v2_articleN.md` expects no matches.
   Failures are not negotiable.
2. **Substance audit (Section 13).** Read every sentence asserting what the system
   does/does not do and confirm it against current code and data. Highest risk:
   honest-limits lists (name the specific gap, not a broad capability), tense vs
   shipped state (past tense for fixed problems), and diagram labels / counts /
   alt-text (Article 12: diff diagram labels against section headers; alt-text must
   match the CURRENT figure). Read the draft as an adversary.
3. **Editorial review (Section 22, six points).** Strongest insight, title quality,
   structural weaknesses, LinkedIn readability, diagram quality, banner quality.

## Phase 6 - LinkedIn promo post

Write `blogs/blog_v2_articleN_linkedin_post.md` (Section 21): lead with the INSIGHT
not "I published." Hook (mobile-preview length) -> short unpack -> one quotable
lesson -> one open question -> 5-8 hashtags. Emoji <= 3-4, engineering tone. Same
voice rules apply.

## Phase 7 - Publish handoff and record

Publishing to LinkedIn and uploading images is the author's manual step; you do not
post. After it is live:

- Capture the published URL.
- Update memory: write/refresh the `project_v2_articleN_*` memo (status, URL, locked
  decisions, known caveats) and add the line to `MEMORY.md`.
- Feed lessons back into the source of truth: add a "Lessons from Article N" bullet
  set to `blogs/blog_v2_series_guidelines.md` Section 16, and update the series plan.
  The guidelines doc and the memory files are updated together (Section 18).
- Keep the per-article sources file (`blogs/blog_v2_articleN_sources.md`) current;
  only the Section 11 shortlist ships in the article (Section 19).

---

## Source of truth

- `blogs/blog_v2_series_guidelines.md` - all rules (this skill points into it).
- `blogs/blog_v2_series_plan.md` - per-article topics and order.
- `blogs/blog_v2_image_pipeline.md` - house style + accuracy gate for any
  ChatGPT-path image.
- `reference_image_pipeline`, `feedback_blog_writing_voice`,
  `feedback_plan_before_drafting`, `feedback_confirm_before_overwrite`,
  `feedback_no_plagiarism_sourcing`, `feedback_token_economy_tooling` (memory).

If this skill and the guidelines doc ever disagree, the guidelines doc wins; then
fix this skill.
