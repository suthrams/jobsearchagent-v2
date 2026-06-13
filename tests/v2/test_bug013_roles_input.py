"""BUG-013: role/title inputs must be one-per-line, never comma-split.

Generalizes BUG-011 (locations) to the roles/titles field: a comma can be part of
a single title ("Director, Engineering"), so comma-splitting shatters it. Both the
Start-Run form and the Settings page must use the shared parse_lines_input seam.
"""
from __future__ import annotations

from pathlib import Path

from app.ui.formatting import lines_to_text, parse_lines_input


def test_role_with_internal_comma_survives():
    assert parse_lines_input("Director, Engineering") == ["Director, Engineering"]
    assert parse_lines_input("Staff Engineer\nDirector, Engineering\nSOC Analyst") == [
        "Staff Engineer", "Director, Engineering", "SOC Analyst",
    ]


def test_strips_and_drops_blanks():
    assert parse_lines_input("  SOC Analyst \n\n  Network Analyst  \n") == [
        "SOC Analyst", "Network Analyst",
    ]
    assert parse_lines_input("") == [] and parse_lines_input(None) == []


def test_round_trips():
    roles = ["Staff Engineer", "Director, Engineering"]
    assert parse_lines_input(lines_to_text(roles)) == roles


def _src(view: str) -> str:
    return (Path(__file__).resolve().parents[2]
            / "app" / "ui" / "views" / view).read_text(encoding="utf-8")


def test_start_run_and_settings_do_not_comma_split_roles():
    """Structure guard: neither surface may regress to splitting roles/titles on
    commas; both must go through the shared parse_lines_input seam."""
    for view in ("start_run.py", "settings.py"):
        src = _src(view)
        assert "roles.split(\",\")" not in src, view
        assert "titles_str.split(\",\")" not in src, view
        assert "parse_lines_input" in src, view
