# Testing — strategy, layout, and statistics

This is the **single source of truth** for how jobsearchagent-v2 is tested: the
strategy, how the suite is laid out, how to run it, what a change must add, and the
current headline numbers. Other docs describe a feature's *own* tests inline (e.g.
an ADR's "Tests" section) but point **here** for the suite-wide picture so test
counts live in exactly one place instead of drifting across the repo.

> **The authoritative test count is whatever `python -m pytest tests/` (or CI)
> reports right now.** Any number written in prose — including the snapshot below —
> is a point-in-time figure for orientation, not a contract. If you need the live
> total, run the suite.

---

## 1. Current snapshot (point-in-time)

| Metric | Value | As of |
|---|---:|---|
| Tests passing | ~1048 | 2026-06-11 |
| Skipped | 1 | 2026-06-11 |
| Test files (`tests/v2/`) | ~86 | 2026-06-11 |
| Real API calls in CI | 0 (mock mode) | — |

Update this table only on a deliberate suite-wide refresh; do **not** sprinkle the
number back into other docs. When it drifts, only this row is wrong — which is the
entire point of consolidating it here.

---

## 2. Strategy

The test suite is built around a few load-bearing principles, each tied to a rule
or ADR.

- **Mock mode is the default; no real LLM/API calls in CI.** The Phase-7 gate keys
  off `ANTHROPIC_API_KEY` (ADR-048): unset -> all agents are mocked and the graph
  runs on `MemorySaver`, which is exactly the environment CI and the default
  `pytest` run use. So the whole suite runs offline, deterministically, for free.
  The mock agent side-effects live in one place: `_build_mocked_deps()` in
  `app/api/dependencies.py`.
- **Live-API smoke tests are opt-in.** Tests that hit real Claude / Adzuna are
  marked `@pytest.mark.integration` and are excluded from the default run; invoke
  them explicitly with `-m integration` (they require a populated `.env`). They are
  smoke-level (does the wiring work end to end), not exhaustive.
- **Invariant tests guard load-bearing promises at their seam.** A module-mock unit
  test is not enough for a promise the whole system depends on; those get an
  invariant test that spans the seam and fails the build if the guarantee regresses.
  Examples: the PII-redaction send-side seam (source-scan test, ADR-069), the
  UI-never-opens-the-DB rule (ADR-075), the per-agent model pins
  (`tests/model_pins.json` + `test_model_pins.py`, ADR-058). See the standing
  guidance on testing invariants for critical concerns.
- **Forcing-function tests make a class of regression impossible to reintroduce
  silently.** Two recurring uses: (a) every newly-wired observability table gets a
  test that fails if the wiring is removed (ADR-074 family — a build that drops
  below the expected number of security-event emit sites fails); (b) every critical
  *runtime* bug gets an RCA in `bugs/` plus a forcing-function test so the same
  defect cannot return unseen (see `bugs/README.md`).
- **PSSR mindset.** Performance / Scalability / Security / Reliability are weighed on
  every change; tests cover the dimension a change actually touches (e.g.
  never-lose-the-run behavior on a discovery filter failure, cost-cap rejection on a
  config write).

---

## 3. Layout

```
tests/
  v2/                       the v2 suite (one file per ADR or topic)
    test_adrNNN_*.py        feature/decision tests, named by ADR
    test_*.py               topical suites (workflow_nodes, model_registry,
                            config_service, api_*, active_tracks, ...)
    test_model_pins.py      invariant: live registry vs tests/model_pins.json
  model_pins.json           the pinned per-agent (provider, model) assignment
  ...                       shared fixtures / conftest
```

Conventions:

- **Naming.** A test file is either `test_adrNNN_<slug>.py` (tied to a specific
  decision — the fastest way to find a feature's tests) or `test_<topic>.py` for a
  cross-cutting area (nodes, providers, config, api).
- **No network in unit tests.** `httpx` is patched (or the scraper/provider is
  mocked) so unit tests never touch the wire; fixtures mirror the live API response
  shapes, with the date they were verified noted in the test module.
- **Mock side-effects are centralized**, not redefined per test — see
  `_build_mocked_deps()` and the `_make_*_side_effect` helpers in
  `app/api/dependencies.py`.
- **UI is smoke-tested headlessly.** The `smoke-test-ui` skill runs every Streamlit
  view through `streamlit.testing.v1.AppTest` (every `render(ctx)` actually
  executes), with an optional real-browser Playwright pass. Run it after touching
  `app/ui/`.

---

## 4. Running the suite

```bash
python -m pytest tests/                 # full suite, mock mode (no real API calls)
python -m pytest tests/ -m integration  # live-API smoke tests (needs .env)
python -m pytest tests/v2/test_adr098_per_profile_ats.py -q   # one file
python .claude/skills/smoke-test-ui/smoke_ui.py               # headless UI smoke
```

The default run must be green before any commit (a standing workflow rule).

---

## 5. What a change must add

A change is not "done" on code alone. Depending on what it touches:

- **A load-bearing promise / new seam** -> an invariant test at the seam (not just a
  mocked unit test).
- **A newly-wired observability table or a guardrail with a fixed emit-site count**
  -> a forcing-function test that fails if the wiring is removed.
- **A critical runtime bug fixed** -> an RCA in `bugs/` + a forcing-function test
  (template in `bugs/README.md`).
- **A per-agent model reassignment** -> update `tests/model_pins.json` in a separate
  commit, only after `pytest -m integration` + a semantic-drift check; never edit the
  pin just to silence the test (ADR-058).
- **A UI view / nav / formatting change** -> re-run `smoke-test-ui` (15/15 clean).

---

## 6. References

- ADR-048 — API-key presence as the live/mock-mode gate.
- ADR-058 — per-agent model pins + the pin-drift invariant test.
- ADR-069 — PII redaction seam + its source-scan invariant.
- ADR-074 — wired observability tables + their forcing-function tests.
- ADR-075 — the UI-reads-through-the-API invariant.
- `bugs/README.md` — RCA convention + forcing-function-test template.
- `.claude/skills/smoke-test-ui/` — the headless UI render check.
- The ADR index (`docs/architecture/adr/ADR-000-index.md`) and CI — the live source
  of truth for counts as the suite grows.
