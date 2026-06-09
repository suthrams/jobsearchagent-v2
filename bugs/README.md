# Bug RCAs

Root-cause analyses for critical *runtime* bugs -- the ones that reach a user as a
crash or wrong behavior because no test caught them first. Each gets its own file
and, wherever possible, a forcing-function test so the same class of bug cannot
return silently.

This is distinct from `docs/incidents/` (operational/postmortem log). `bugs/` is
specifically for code-level runtime defects and their static/dynamic guards.

## When to add an entry

Add an RCA when a bug:
- reached runtime (was not caught by the test suite or a review), AND
- **the user reported it** during a session, OR it is critical (crashes a
  screen/flow, corrupts data, leaks something, or produces a materially wrong
  result).

Owner directive (2026-06-08): **write an RCA for every bug the user finds**, not
only the critical ones — a user-found bug is by definition one that slipped past
testing, so the "why it was not caught" section is always worth recording. Lower-
severity friction bugs still get an entry (e.g. BUG-005); note when no automated
forcing function is feasible and why.

Skip trivial typos fixed in the same breath with no test gap to explain.

## Naming

`BUG-NNN-short-slug.md`, zero-padded, monotonically increasing. Newest bug takes
the next free number; never reuse a retired number.

## Required sections

Use `_TEMPLATE.md`. Every RCA must answer four questions in order:

1. **What happened** -- symptom + traceback/observed behavior.
2. **Root cause** -- the actual defect, not the symptom.
3. **Why it was not caught** -- the specific test/review gap (this is the most
   valuable section; it is what drives prevention).
4. **Prevention** -- the fix AND the forcing function (test/lint/process) that
   makes recurrence fail the build. Link the test file.

ASCII only (repo convention).

## Index

| ID | Title | Severity | Status | Forcing function |
|----|-------|----------|--------|------------------|
| [BUG-001](BUG-001-ui-missing-httpx-import.md) | UI view crashes: `httpx` referenced but not imported | High | Fixed | `tests/v2/test_ui_undefined_names.py` |
| [BUG-002](BUG-002-job-focused-clinic-chat-clobbered.md) | Job-focused clinic chat edits frozen + clobbered on save | Critical | Fixed | `tests/v2/test_resume_clinic_repository.py::test_set_decision_without_payload_preserves_chat_edits` |
| [BUG-003](BUG-003-renderer-duplicates-merged-bullets.md) | Resume export duplicates bullets on a bullet-merging rewrite | High | Fixed | `tests/v2/test_resume_text_renderer.py::test_rewrite_merging_two_bullets_collapses_without_duplication` |
| [BUG-004](BUG-004-pdf-export-garbled-text.md) | PDF export garbled (literal `&middot;`, `bullet` markers, notdef boxes) | High | Fixed | `tests/v2/test_resume_text_renderer.py::test_render_pdf_text_has_no_literal_entities_or_bullet_word` |
| [BUG-005](BUG-005-chat-input-send-disabled-and-not-cleared.md) | Chat input: Send stays disabled while typing; box not cleared after send | Medium | Fixed | none feasible (Streamlit interaction; structural fix) |
| [BUG-006](BUG-006-profile-resets-on-navigation.md) | Selected profile resets on page navigation | High | Fixed | `tests/v2/test_ui_structure.py::test_profile_switcher_is_single_source_of_truth` |
| [BUG-007](BUG-007-live-run-autorefresh-lambda-fragment.md) | Live Run Monitor never auto-refreshes (lambda passed to st.fragment) | Medium | Fixed | `tests/v2/test_ui_structure.py::test_live_monitor_autorefresh_uses_stable_fragment` |
| [BUG-008](BUG-008-profile-reset-recurrence.md) | Profile selector STILL resets on navigation (BUG-006 fix insufficient) | High | Fixed | `tests/v2/test_ui_structure.py::test_profile_switcher_is_single_source_of_truth` |
| [BUG-009](BUG-009-live-run-unreachable-after-declutter.md) | Live run unreachable from other screens after sidebar declutter | Medium | Fixed | none (runtime reachability; active-run chip jump restored) |
