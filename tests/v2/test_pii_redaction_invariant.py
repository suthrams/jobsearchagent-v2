"""PII redaction invariant at the LLM context seam (ADR-069).

These tests are a forcing function, not a behavioral test (cf. the model-pin
invariant in test_model_pins.py). They assert the system-wide property that
direct identifiers and raw_text never enter an agent LLM context, so the A1/A2
gaps documented in docs/architecture/pii_data_flow.md cannot silently reopen.

Two layers:
  1. The pure clinic fidelity-context builder is exercised directly.
  2. A source scan asserts every "resume_profile" placed into an agent context
     routes through redact_pii_for_llm / trim_resume_profile, with an explicit
     allowlist for the kickoff site that initializes the profile in WorkflowState
     to None (not an LLM context). Since ADR-070, load_resume also stores the
     REDACTED profile in state, so it passes via the helper branch like any agent
     context rather than being allowlisted.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.context_trimmer import PII_NAME_PLACEHOLDER, redact_pii_for_llm
from app.services.resume_clinic_runner import build_fidelity_context_for_overhaul

APP_DIR = Path(__file__).resolve().parents[2] / "app"

# Sites that write resume_profile into WorkflowState (NOT an LLM context) with a
# value that is not itself a redaction-helper call. Each entry is (relative posix
# path, allowed right-hand-side substrings).
#
# ADR-070 note: load_resume.py was previously allowlisted because it stored the
# full profile (`model_dump()`). It now stores `redact_pii_for_llm(...)` so
# raw_text + direct identifiers never reach state_json / the checkpoints blob, and
# it passes the scan via the redaction-helper branch like any agent context - so it
# is no longer in this allowlist. Only workflows.py remains: it initializes the
# state key to None at kickoff.
_STATE_WRITE_ALLOWLIST = {
    "app/api/routers/workflows.py": ("None",),
}

# project_resume_for_scoring (ADR-086) is a sanctioned wrapper: it calls
# trim_resume_profile internally, so the PII seam holds at sites that use it.
_REDACTION_HELPERS = (
    "redact_pii_for_llm(", "trim_resume_profile(", "project_resume_for_scoring(",
)
_RESUME_PROFILE_KEY = re.compile(r'"resume_profile"\s*:')


def test_clinic_fidelity_context_redacts_cached_profile_but_keeps_raw_text():
    """The clinic Fidelity Reviewer is the sanctioned raw_text path (ADR-015):
    raw_text is preserved TOP-LEVEL for evidence-binding, while the cached
    profile block is redacted like every other agent context (ADR-069)."""
    parsed_profile = {
        "name": "Jane Smith",
        "email": "jane@example.com",
        "location": "Atlanta, GA",
        "raw_text": "JANE SMITH ... full resume blob",
        "skills": ["Python"],
        "experience": [{"company": "Acme", "title": "Eng"}],
    }
    ctx = build_fidelity_context_for_overhaul(
        clinic_id="c1",
        resume_id="r1",
        parsed_profile=parsed_profile,
        raw_text=parsed_profile["raw_text"],
        rewrites=[{
            "original_text": "Built things",
            "suggested_text": "Built scalable things",
            "supporting_evidence": "Acme: built platform",
            "claim_type": "reframe",
            "section_label": "Experience",
        }],
    )

    cached = ctx["_cached"]["resume_profile"]
    assert "raw_text" not in cached
    assert cached["name"] == PII_NAME_PLACEHOLDER
    assert cached["email"] is None
    assert cached["location"] is None
    # reasoning fields preserved
    assert cached["skills"] == ["Python"]
    # the one sanctioned raw_text path: top-level, outside the profile block
    assert ctx["raw_text"] == parsed_profile["raw_text"]


def test_load_resume_stores_redacted_profile_in_state():
    """ADR-070: load_resume writes the REDACTED profile into WorkflowState, so
    raw_text + direct identifiers never reach state_json / the checkpoints blob."""
    from app.schemas.resume_profile import ResumeProfile
    from app.workflows.nodes.load_resume import make_load_resume_node

    profile = ResumeProfile(
        resume_id="r1",
        raw_text="JANE SMITH full resume blob",
        name="Jane Smith",
        email="jane@example.com",
        location="Atlanta, GA",
        headline="Security Architect | (555) 123-4567",
        skills=["Python"],
        parsed_at="2026-01-01T00:00:00.000Z",
    )

    class _Repo:
        def get_by_id(self, _id):
            return {"parsed_profile_json": profile.model_dump_json(), "version": 2}

    node = make_load_resume_node(resume_parser=None, observability=None, resume_repo=_Repo())
    out = node({"workflow_id": "wf1", "resume_id": "r1"})

    rp = out["resume_profile"]
    assert "raw_text" not in rp
    assert rp["name"] == PII_NAME_PLACEHOLDER
    assert rp["email"] is None
    assert rp["location"] is None
    assert "[PHONE]" in rp["headline"]   # ADR-069 addendum scrub still applies
    assert rp["skills"] == ["Python"]    # reasoning fields preserved
    assert out["resume_version"] == 2


def test_no_agent_context_carries_an_unredacted_profile():
    """Every "resume_profile": site in app/ either routes through a redaction
    helper or is an allowlisted WorkflowState write. A new agent context that
    forgets the helper fails here."""
    violations: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        rel = py.relative_to(APP_DIR.parents[0]).as_posix()
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if not _RESUME_PROFILE_KEY.search(line):
                continue
            if any(h in line for h in _REDACTION_HELPERS):
                continue
            allowed = _STATE_WRITE_ALLOWLIST.get(rel)
            if allowed and any(rhs in line for rhs in allowed):
                continue
            violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "resume_profile placed into a context without redact_pii_for_llm/"
        "trim_resume_profile (ADR-069). Route it through the redaction seam, or "
        "if it is a WorkflowState write, add it to _STATE_WRITE_ALLOWLIST:\n  "
        + "\n  ".join(violations)
    )


def test_state_write_allowlist_sites_still_exist():
    """Guard the allowlist itself: if a listed site is moved/renamed, this test
    fails so the allowlist is re-justified rather than silently stale."""
    for rel, rhs_options in _STATE_WRITE_ALLOWLIST.items():
        path = APP_DIR.parents[0] / rel
        assert path.exists(), f"allowlisted PII state-write file missing: {rel}"
        text = path.read_text(encoding="utf-8")
        assert _RESUME_PROFILE_KEY.search(text), (
            f"{rel} no longer assigns resume_profile; remove it from the allowlist"
        )
        assert any(rhs in text for rhs in rhs_options), (
            f"{rel} resume_profile RHS changed; re-verify it is a state write, "
            f"not an LLM context, before updating the allowlist"
        )
