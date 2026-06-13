# BUG-013: Roles/titles input comma-splits, corrupting titles that contain a comma

- **Severity:** Medium
- **Status:** Fixed
- **Reported:** 2026-06-12 (user request during the validation session)
- **Fixed:** 2026-06-12
- **Area:** `app/ui/views/start_run.py`, `app/ui/views/settings.py`, `app/ui/formatting.py`
- **Introduced by:** original Start-Run / Settings forms (roles were always comma-separated). The same class was already fixed for locations in BUG-011 but not generalized to roles.

## 1. What happened

The roles/titles field on both the Start-Run form and the Settings page was a
comma-separated text box parsed with `roles.split(",")` / `titles_str.split(",")`.
A role whose title legitimately contains a comma -- e.g. "Director, Engineering"
-- was shattered into two bogus roles ("Director" + "Engineering"), corrupting the
search criteria. Locations had this exact bug fixed in BUG-011 (one-per-line), but
the fix was not generalized to the roles field.

## 2. Root cause

Comma was used as the item separator for a field whose items can themselves
contain commas. This is identical to BUG-011 (locations: "Atlanta, GA"). The
roles field never moved onto the one-per-line seam that locations adopted.

## 3. Why it was not caught

- BUG-011 fixed locations specifically and added `parse_locations_input` as "the
  shared seam," but the seam was only applied to locations -- roles kept their own
  `split(",")`. The fix was treated as a locations fix, not as the general class
  "list-style fields must not comma-split."
- No test asserted roles-with-internal-commas, and no structure test forbade
  `roles.split(",")`, so the parallel defect sat unnoticed until the user asked for
  one-per-line roles.

## 4. Prevention

- **The fix:** generalize the seam. `app/ui/formatting.py` now exposes
  `parse_lines_input` / `lines_to_text` (general, one-item-per-line);
  `parse_locations_input` / `locations_to_text` remain as aliases. Both the
  Start-Run form and the Settings page now render roles/titles as a one-per-line
  `text_area` and parse with `parse_lines_input` -- the same seam locations use.
- **Forcing function:** `tests/v2/test_bug013_roles_input.py` -- asserts a
  comma-containing title survives parsing AND a structure guard that neither view
  comma-splits roles/titles (both must use `parse_lines_input`).
- **Generalization:** the guard covers the whole class (any list-style field on
  either surface), not just roles. The shared seam means a future list field
  inherits the correct behavior, and the structure test fails the build if either
  surface drifts back to comma-splitting.
