"""BUG-011: the locations input must be split ONE PER LINE, never on commas, so a
"City, State" location survives. The Settings page used to comma-split, shattering
"Atlanta, GA" into "Atlanta" + "GA". Both the Start-Run form and the Settings page
now go through the single parse_locations_input / locations_to_text seam.
"""
from __future__ import annotations

from app.ui.formatting import locations_to_text, parse_locations_input


def test_city_state_survives_not_comma_split():
    # The exact defect: a comma is PART of the location, not a separator.
    assert parse_locations_input("Atlanta, GA") == ["Atlanta, GA"]
    assert parse_locations_input("Atlanta, GA\nRemote\nNew York, NY") == [
        "Atlanta, GA", "Remote", "New York, NY"]


def test_one_per_line_strips_and_drops_blanks():
    assert parse_locations_input("  Boston, MA \n\n  Remote  \n") == ["Boston, MA", "Remote"]
    assert parse_locations_input("") == []
    assert parse_locations_input(None) == []


def test_round_trips_with_locations_to_text():
    locs = ["Atlanta, GA", "Remote", "Washington, DC"]
    assert parse_locations_input(locations_to_text(locs)) == locs


def test_locations_to_text_is_newline_joined():
    # Newline, never comma -- so the rendered box round-trips through the parser.
    assert locations_to_text(["Atlanta, GA", "Remote"]) == "Atlanta, GA\nRemote"
    assert locations_to_text([]) == ""
    assert locations_to_text(None) == ""
