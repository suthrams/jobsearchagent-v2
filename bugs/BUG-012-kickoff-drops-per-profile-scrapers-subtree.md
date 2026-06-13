# BUG-012: Workflow kickoff drops the per-profile scrapers subtree (ATS targeting reverts to system default)

- **Severity:** High
- **Status:** Fixed
- **Reported:** 2026-06-12 (found during a live-validation prep, profile 1 "Vishal - Cyber grad")
- **Fixed:** 2026-06-12
- **Area:** `app/api/routers/workflows.py` (start_workflow kickoff), `app/services/config_service.py`; observed against ADR-098 per-profile ATS targeting
- **Introduced by:** ADR-098 (per-profile ATS company lists). The per-run resolution
  was added to `discover_jobs` + the ATS factory, but the kickoff path that builds
  `effective_config` was never updated to carry the `scrapers` subtree.

## 1. What happened

Profile 1 has a per-profile ATS company list (`scrapers.greenhouse.companies` =
23 cyber boards: huntress, expel, knowbe4...; `scrapers.lever.companies` = 5 cyber
boards) saved in `user_config` and managed from the Settings "Target companies"
section. `ConfigService.get_effective_config("1")` resolves these correctly.

But on any run launched from the Start-run screen, ATS discovery queried the
**system** curated batch (general tech: affirm, airbnb, anthropic...) instead of
the profile's cyber boards. The per-profile targeting silently did nothing.
CLAUDE.md's claim that "an edit applies next run with NO /config/reload" was not
true through the UI kickoff path.

## 2. Root cause

The kickoff `effective_config` is hand-assembled subtree-by-subtree in the UI.
`app/ui/views/start_run.py` builds it with only `scoring` + `search` keys (never
`scrapers`), and `app/api/routers/workflows.py::start_workflow` then injected only
the `agents` snapshot before persisting it as the run state. So
`state["effective_config"]` had no `scrapers` key.

`discover_jobs` reads `state["effective_config"]["scrapers"]` -> `{}` and passes it
to the ATS factory. The factory (`_build_real_deps._ats_factory`) does
`build_ats_scrapers(roles, scrapers_cfg or _scrapers_cfg)` -- and `{} or _scrapers_cfg`
falls back to the deps-time **system** config. So the profile's list was replaced
by the system default precisely because the kickoff omitted the subtree.

The defect is one layer up from ATS: the kickoff resolves an *incomplete*
effective config. Any per-profile subtree the UI does not explicitly assemble is
lost the same way -- scrapers is just the one that had observable behavior.

## 3. Why it was not caught

ADR-098 shipped with tests (`tests/v2/test_adr098_per_profile_ats.py`), but each
tested a piece in isolation, never the kickoff-to-state seam:

- `test_discover_jobs_passes_effective_config_scrapers_to_factory` constructs the
  node's input state **by hand with scrapers already present**, then asserts the
  node forwards it. It proved the node seam, and assumed something upstream had
  populated `effective_config["scrapers"]`. Nothing did.
- `test_effective_config_override_replaces_default_list` proved
  `ConfigService.get_effective_config` merges the override -- but the kickoff
  never calls that on the run path; the UI builds the config itself.
- `test_build_ats_scrapers_is_per_run_replace` proved the factory honors whatever
  config it is handed -- again, downstream of the gap.

No test exercised "POST /workflows with a partial effective_config" and asserted
the persisted run state carried the profile's scrapers. The integration seam
between kickoff and the first node was the blind spot -- the classic "every unit
passed, the wire between them was never asserted."

## 4. Prevention

- **The fix:** resolve the FULL per-run config server-side at kickoff. Added
  `ConfigService.resolve_run_config(user_id, run_overrides)`, which deep-merges the
  kickoff's partial config OVER the profile's complete `get_effective_config`
  (overrides win per key, limits re-enforced). `start_workflow` now calls it after
  injecting the agents snapshot, so the persisted `effective_config` carries every
  per-profile subtree -- scrapers included -- while caller-supplied scoring/search
  still win.
- **Forcing function:**
  `tests/v2/test_adr098_per_profile_ats.py::test_resolve_run_config_preserves_profile_scrapers_when_body_omits_them`
  (a body of only scoring+search must still yield the profile's scrapers list) and
  `::test_resolve_run_config_body_scrapers_override_wins` (an explicit body override
  still replaces). These pin the kickoff seam, not just the node seam.
- **Generalization:** the fix closes the whole class, not just scrapers. Because
  the kickoff now starts from the profile's complete config and treats the body as
  overrides, any future per-profile subtree the UI forgets to assemble is preserved
  automatically. The reported case (profile 1 cyber ATS list) is the test, not the
  target -- consistent with "fix the product, not the profile."
