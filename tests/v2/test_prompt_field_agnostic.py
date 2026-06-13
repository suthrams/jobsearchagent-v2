"""Field-agnostic forcing function (profile-specifics in data, not prompts).

The relevance filter was made field-agnostic (its own test pins that). This guard
extends the same standard to the other shared prompts that carry field-specific
examples: each must label them illustrative AND instruct deriving the candidate's
field from their profile / not assuming an industry. Prevents a regression to a
prompt that silently assumes a tech/cyber candidate.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROMPTS = Path(__file__).resolve().parents[2] / "app" / "prompts" / "agents"


def _norm(name: str) -> str:
    """Lowercased, whitespace-collapsed prompt text so phrase checks survive the
    line wrapping inherent to prompt files."""
    raw = (_PROMPTS / name).read_text(encoding="utf-8").lower()
    return re.sub(r"\s+", " ", raw)

# Prompts that legitimately carry field-specific EXAMPLES and must label them.
_FIELD_EXAMPLE_PROMPTS = [
    "resume_parser.txt",
    "resume_reviewer.txt",
    "resume_chat.txt",
    "tailoring_agent.txt",
]


@pytest.mark.parametrize("name", _FIELD_EXAMPLE_PROMPTS)
def test_field_specific_examples_are_labelled_illustrative(name):
    low = _norm(name)
    # Examples must be flagged as illustrative...
    assert "illustrative" in low, f"{name}: field examples not labelled illustrative"
    # ...and the prompt must say not to assume an industry / to derive the field.
    assert ("do not assume any particular industry" in low
            or "derive" in low), f"{name}: missing field-agnostic derive/do-not-assume guard"


@pytest.mark.parametrize("name", _FIELD_EXAMPLE_PROMPTS)
def test_cybersecurity_only_appears_as_an_example(name):
    """If cybersecurity is mentioned, it must be flagged as an example, never the
    assumed default field."""
    low = _norm(name)
    if "cybersecurity" in low or "security analyst" in low:
        assert "illustrative" in low or "example" in low, (
            f"{name}: cyber content must be flagged illustrative/example")
