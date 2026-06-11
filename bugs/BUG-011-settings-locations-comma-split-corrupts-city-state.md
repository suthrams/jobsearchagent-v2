# BUG-011: Settings locations field comma-splits, corrupting "City, State"

- **Severity:** High
- **Status:** Fixed
- **Reported:** 2026-06-11
- **Fixed:** 2026-06-11
- **Area:** `app/ui/views/settings.py`, `app/ui/views/start_run.py`,
  `app/ui/formatting.py`
- **Introduced by:** latent since the Settings UI was written; the Start-Run form
  was later corrected to one-per-line (ADR-064) but the Settings page was not, so
  the two surfaces diverged.

## 1. What happened

A profile-1 user added locations in the new Start-Run search form and they "did
not appear to be saved even after clicking save the settings." Two distinct gaps:

1. The Start-Run form persists settings to the profile ONLY when the easily-missed
   "Save these settings as my defaults for future runs" checkbox (default off) is
   ticked AND a run is started. There was no standalone "save to profile" action,
   so location edits looked unsaved.
2. The Settings page DID have an immediate "Save locations" button, but that field
   was **comma-split**: `locations_str.split(",")`. A location like `Atlanta, GA`
   was shattered into two bogus entries `Atlanta` and `GA` on save.

## 2. Root cause

Locations legitimately contain a comma (`City, State`), so they must be split
ONE PER LINE, never on commas. ADR-064 established this and the Start-Run form
honored it (`"\n".join` / `splitlines`), but the Settings page kept the old
comma-split (`", ".join` for display, `split(",")` for save). The two surfaces
each parsed locations their own way, so one corrupted what the other stored.
Roles/titles, which do not contain commas, stay comma-separated by design.

## 3. Why it was not caught

- The location parsing was **inline** in each view (no shared function), so there
  was no single seam to test; the two surfaces drifted with nothing to flag it.
- No test fed a realistic `City, State` location through the Settings save path,
  so the shatter was invisible.
- Streamlit views are not exercised by the suite, so the inline `split(",")`
  never ran in a test.

## 4. Prevention

- **The fix:**
  - New shared seam `parse_locations_input(text)` / `locations_to_text(list)` in
    `app/ui/formatting.py` (pure, one-per-line, never comma-split). BOTH the
    Settings page and the Start-Run form now go through it, so they cannot drift.
  - Settings `search.locations` is now a one-per-line text area (label + help
    updated); titles stay comma-separated (no commas in a role).
  - Start-Run form gains a standalone **"Save settings to my profile"**
    `form_submit_button` that persists the form's roles/locations/filters to the
    acting profile (via the `?user_id=` config seam) WITHOUT starting a run, and
    invalidates the config cache so the saved values read back immediately.
- **Forcing function:** `tests/v2/test_bug011_locations_input.py` -
  `test_city_state_survives_not_comma_split` (the exact "Atlanta, GA" case),
  plus strip/blank-dropping and round-trip tests through the shared seam.
- **Generalization:** catches the whole class "two UI surfaces parse the same
  user input differently" by collapsing locations parsing to one tested seam -
  not just the one reported field.
