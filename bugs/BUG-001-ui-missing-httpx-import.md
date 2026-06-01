# BUG-001: UI view crashes -- `httpx` referenced but never imported

- **Severity:** High (crashes the Workflow Detail screen on a common action)
- **Status:** Fixed
- **Reported:** 2026-06-01
- **Fixed:** 2026-06-01
- **Area:** `app/ui/views/workflow_detail.py`, `app/ui/views/resume_clinic.py`
- **Introduced by:** Phase 4 UI refactor migrations `421d1d9` (Workflow Detail) and
  `2dfb2bf` (Resume Clinic). The `import httpx` was added to the monolithic
  `streamlit_app.py` in `385a697` (the 60s -> 180s tailoring-timeout fix), then left
  behind when those view bodies were lifted into their own modules.

## 1. What happened

Rendering the Workflow Detail screen and triggering a tailoring draft raised:

```
File "app/ui/streamlit_app.py", line 214, in <module>
    _render_view(ctx)
File "app/ui/views/workflow_detail.py", line 418, in render
    except httpx.ReadTimeout:
           ^^^^^
NameError: name 'httpx' is not defined
```

The module references `httpx.ReadTimeout` (workflow_detail.py lines 418 and 435) and
`httpx.HTTPStatusError` (resume_clinic.py line 421), but neither module imported
`httpx`. The same defect was latent in Resume Clinic -- it just had not been hit yet
because its `except` branch had not been reached in normal use.

## 2. Root cause

A dropped import during the UI refactor (`docs/architecture/ui_refactor_plan.md`).
The 3.6K-line `streamlit_app.py` was split into per-screen `views/<name>.py` modules
by lifting each screen's body "verbatim" into `render(ctx)`. The view *bodies* moved;
the top-of-file `import httpx` they depended on did not travel with them. Each new
view module rebuilt its own import block by hand and missed `httpx`.

The reference survives import because Python resolves global names lazily: `httpx` in
`except httpx.ReadTimeout:` is not looked up at import time, nor even when `render()`
runs -- only at the moment an exception propagates out of the `try` and Python
evaluates the `except` type to test a match. So the module imports clean and the
screen renders clean; the `NameError` fires only when a real tailoring call raises
inside that specific `try`.

## 3. Why it was not caught

Two UI test layers existed, and the defect stepped over both:

- **Structural import tests** (`tests/v2/test_ui_structure.py`) only
  `importlib.import_module(...)` each view. Importing a module does not resolve names
  referenced inside its function bodies, so an undefined global in `render()` imports
  without error.

- **Headless smoke test** (`.claude/skills/smoke-test-ui/smoke_ui.py`) does execute
  each `render(ctx)` via Streamlit `AppTest`, so it would catch a missing import at
  the top level of `render()`. But the offending line is doubly shielded:
  1. it sits inside an `if st.button("Generate new draft", ...):` branch, and
     `st.button()` returns `False` in a headless run with no simulated click, so the
     `try`/`except` body is never entered; and
  2. even if entered, `httpx` is only looked up when the wrapped call actually raises
     and Python evaluates the `except` type.

Net: the defect requires a real user click **plus** the backend call raising/timing
out. That combination is only reachable in a live click-through, which was the one
open item still outstanding from the UI refactor.

## 4. Prevention

- **The fix:** added `import httpx` to both `app/ui/views/workflow_detail.py` and
  `app/ui/views/resume_clinic.py`.

- **Forcing function:** `tests/v2/test_ui_undefined_names.py`. It statically scans
  every module under `app/ui/` using the stdlib `symtable` scope analyzer (the same
  one CPython uses) and fails the build on any free name that is neither a
  module-level binding nor a builtin. `symtable` models parameters, comprehensions,
  nested functions, and `global`/`nonlocal` correctly, so it flags genuine undefined
  names with no false positives. Verified: with `import httpx` removed the test fails
  naming `workflow_detail.py: ['httpx']`; restored, it passes.

- **Generalization:** this guards the whole class of "view body lifted during the
  refactor but an import (or other name) left behind," not just `httpx`. It catches
  any undefined name anywhere in `app/ui/`, regardless of whether the line is behind
  a click or an `except`. Optional future hardening (not done): extend
  `smoke_ui.py` to simulate the button click with the API mocked to raise
  `httpx.ReadTimeout`, exercising the `except` branch dynamically.
