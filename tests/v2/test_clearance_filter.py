"""Unit tests for the security-clearance predicate (ADR-094)."""
import pytest

from app.services.clearance_filter import requires_clearance


@pytest.mark.parametrize("text", [
    "Active TS/SCI clearance required",
    "Must hold a Secret clearance",
    "Top Secret clearance with polygraph",
    "Ability to obtain a security clearance",
    "DoD clearance preferred",
    "Requires an active security clearance",
    "Position requires a TS-SCI clearance",
    "Full-scope polygraph required",
    "Interim Secret clearance acceptable",
])
def test_detects_clearance_requirements(text):
    assert requires_clearance(text) is True


@pytest.mark.parametrize("text", [
    "Clearance sale on developer tools",          # "clearance" but not security
    "Our secret sauce is great engineering",      # "secret" but not clearance
    "Security+ certification preferred",          # CompTIA cert, not a clearance
    "Build trust with our customers",             # "trust" but not "public trust" clearance
    "No clearance needed for this role",
    "",
])
def test_ignores_false_positives(text):
    assert requires_clearance(text) is False


def test_title_alone_can_signal():
    assert requires_clearance("", "Software Engineer - TS/SCI") is True


def test_none_inputs_are_safe():
    assert requires_clearance(None, None) is False
