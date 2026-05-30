---
name: smoke-test-ui
description: >-
  Smoke-test the Streamlit UI: render all 15 screens headlessly and confirm none
  raises a Python exception. Use after touching app/ui/ (views, nav, components,
  formatting, the entrypoint) or to verify the UI refactor did not break a screen.
  Optionally adds a real-browser screenshot pass for visual confirmation.
---

# Smoke-test the Streamlit UI

The UI is a thin entrypoint (`app/ui/streamlit_app.py`) that dispatches each view
in `app.ui.nav.NAV_VIEWS` through `app/ui/views/REGISTRY` (see
`docs/architecture/ui_refactor_plan.md`). The fast, reliable way to confirm every
screen still renders is Streamlit's headless **AppTest** harness — it executes
each view's `render(ctx)` for real, so a missing import, broken dispatch, or
signature drift surfaces as an exception. A browser screenshot only proves the
page paints; AppTest proves every view's code runs.

## 1. Headless render check (the primary smoke test)

Recommended: start the backend first so config / users / providers reads return
real data. The harness still passes without it (api calls degrade to the offline
fallback), but with it you exercise the API-backed branches too.

```bash
# from the project root
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &
# wait until /config returns 200 (usually ~1s):
#   curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/config

python .claude/skills/smoke-test-ui/smoke_ui.py
```

Expected: `15/15 screens rendered with no unhandled exception.` (exit 0). Any
`XX <view> FAIL -> <exception>` line is a regression — the message names the view
and the error.

The harness pulls a real `workflow_id` / `job_id` from `data/v2.db` so Workflow
Detail and Job Detail render their data-heavy paths, not just the empty picker.

Tear the backend down when finished (Windows / PowerShell):

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## 2. Optional: real-browser visual pass

Confirms the app actually paints (a blank frame is a launch failure AppTest can't
see). Needs `playwright` (installed; chromium is cached).

```bash
python -m streamlit run app/ui/streamlit_app.py --server.port 8501 \
    --server.headless true --browser.gatherUsageStats false &
# wait until http://127.0.0.1:8501/ returns 200
```

Then drive it with Playwright (sync API): `goto("http://localhost:8501/")`,
`wait_for_selector("text=Job Search Agent v2")`, a ~3.5s settle, then
`screenshot(full_page=True)`. Navigate by clicking the sidebar radio labels —
`page.get_by_text("Settings", exact=True).first.click()` — and screenshot each.
**Look at the screenshots.** Low-PII screens to favour: Settings, Cost Dashboard,
Start New Run. Workflow History / detail screens show job titles + company names
(PII) — fine for local review, do not publish. Stop the server (port 8501) after.

## Environment notes

- Streamlit 1.56 (`streamlit.testing.v1.AppTest`). Windows 11; PowerShell + Bash.
- `data/v2.db` must exist (it is gitignored; the harness reads it directly).
- The backend in mocked mode (no `ANTHROPIC_API_KEY`) still serves the read
  endpoints the UI needs, so the smoke test does not need real API keys.
- Deprecation warnings (`use_container_width`) and `missing ScriptRunContext`
  notices in the output are normal AppTest noise, not failures.

## Verified

2026-05-30, after the UI refactor (entrypoint 3,665 -> ~217 lines, all 15 views
moved to `app/ui/views/`): AppTest 15/15 clean; Playwright screenshots of Workflow
History, Settings, Cost Dashboard, and Start New Run all painted correctly with
real data.
