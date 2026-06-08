# BUG-003: Resume export duplicates bullets when a rewrite merges two source bullets

- **Severity:** High (materially wrong, unprofessional output across every export format)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/services/resume_text_renderer.py` (`_replace_or_append_bullet`)
- **Introduced by:** ADR-066 (original renderer) - latent until a rewrite merged bullets

## 1. What happened

The exported resume (all formats - md/txt/html/docx/pdf share the composed model) showed
a 3-bullet role rendered with **5 bullets**. In session `c352756b` the "Series AI" role
listed two stale original bullets ("Translated technical risks..."; "Showcased
cross-functional...") AND an appended rewrite that was the polished merge of those two -
a visible duplication that made the resume look broken.

## 2. Root cause

`_replace_or_append_bullet` matched a rewrite to a source bullet by (1) exact text or
(2) `original_text` being a substring of a bullet, and **appended** the rewrite on no
match (the "never silently drop" rule). When a rewrite's `original_text` *merged two
source bullets into one string* (e.g. "A. B." where A and B are separate bullets), it was
longer than either bullet, so the substring test failed both ways - the rewrite was
appended while both original bullets stayed. Net: two originals + one merged copy = the
duplication. Both the seed converter (`tailored_draft_to_overhaul`) and the chat agent can
emit a bullet-merging rewrite, so this fired in normal use.

## 3. Why it was not caught

The renderer tests covered the matching cases that existed in the author's mental model:
exact match, `original ⊂ bullet` substring, and the unmatched-append fallback. They never
covered the **inverse** shape - `bullet ⊂ original_text`, i.e. one rewrite that subsumes
multiple source bullets - because no test fed a rewrite whose `original_text` concatenated
two bullets. The append-on-no-match branch was tested only with genuinely new content
(where appending is correct), so the branch looked right; the gap was the *missing input
shape*, not a wrong assertion.

## 4. Prevention

- **The fix:** `_replace_or_append_bullet` gained two layered cases before the append
  fallback - (3) merge-collapse: when one or more source bullets are substrings of the
  rewrite's `original_text`, replace the first and delete the rest (min-length guard);
  and (4) a conservative token-overlap (Jaccard) fallback that replaces the single best
  match when it clears a floor and beats the runner-up by a margin (handles light
  rewording of `original_text` across chat turns). Append is preserved only for truly new
  content. ADR-091.
- **Forcing function:**
  `tests/v2/test_resume_text_renderer.py::test_rewrite_merging_two_bullets_collapses_without_duplication`
  (asserts a merged rewrite yields exactly one bullet) and
  `::test_rewrite_token_overlap_replaces_lightly_reworded_original`.
- **Generalization:** these guard the two realistic non-exact match shapes (merge,
  reword). The append fallback remains for unmatched content; an adversarial
  near-duplicate `original_text` could still append, but the token-overlap margin makes
  that rare and the outcome (an extra true bullet) is far less harmful than the
  stale-original duplication this fixes.
