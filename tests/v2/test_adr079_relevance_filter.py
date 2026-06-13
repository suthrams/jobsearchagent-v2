"""ADR-079: reasoning relevance pre-filter between discovery and scoring.

Covers the agent contract, the node's keep/drop narrowing + never-lose-the-run
fallback, the three-way scoring_mode_gate, and the discovery-net widening.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.relevance_filter_agent import RelevanceFilterAgent
from app.providers.llm_client import LLMProviderError, LLMUsage
from app.schemas.relevance_filter import RelevanceFilterResult, RelevanceVerdict
from app.workflows.limits import (
    MAX_DISCOVERED_JOBS,
    get_max_discovered_jobs,
    get_relevance_filter,
)
from app.workflows.nodes.relevance_filter import make_relevance_filter_node
from app.workflows.routers import scoring_mode_gate


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeAgent:
    """Stand-in for RelevanceFilterAgent: returns a fixed result (or raises) and a
    fixed typed usage, so the node can be exercised without a provider."""

    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def run(self, workflow_id, context):
        self.last_context = context
        if self._raises is not None:
            raise self._raises
        return self._result

    def last_call_usage_typed(self):
        return LLMUsage(tokens_input=200, tokens_output=30, cost_usd=0.0004)


def _jobs():
    return [
        {"id": "j1", "title": "Senior Staff Engineer", "company": "A",
         "job_description": "10+ years required", "url": "https://boards/j1"},
        {"id": "j2", "title": "Junior Developer", "company": "B",
         "job_description": "entry level", "url": "https://boards/j2"},
        {"id": "j3", "title": "Software Engineer", "company": "C",
         "job_description": "build things", "url": "https://boards/j3"},
    ]


def _state(**over):
    s = {
        "workflow_id": "wf1",
        "normalized_jobs": _jobs(),
        "resume_profile": {"name": "Cand", "skills": ["Python"]},
        "search_criteria": {"roles": ["Software Engineer"]},
        "effective_config": {"search": {"exclude_senior": True}},
    }
    s.update(over)
    return s


# ── Node: keep/drop narrowing ────────────────────────────────────────────────

def test_node_drops_mismatches_keeps_rest_and_preserves_order():
    result = RelevanceFilterResult(verdicts=[
        RelevanceVerdict(job_id="j1", keep=False, mismatch="too_senior", reason="asks 10+ yrs"),
        RelevanceVerdict(job_id="j2", keep=True),
        # j3 has no verdict -> kept (absence is not a drop signal)
    ])
    node = make_relevance_filter_node(_FakeAgent(result), MagicMock())
    out = node(_state())

    assert [j["id"] for j in out["normalized_jobs"]] == ["j2", "j3"]
    stats = out["discovery_stats"]
    assert stats["relevance_kept"] == 2
    assert stats["relevance_dropped"] == 1
    # The audit entry carries the human-facing title + company + url (for the "why
    # filtered out" UI panel, with a click-through link) alongside the
    # job_id/mismatch/reason.
    assert stats["relevance_drops"][0] == {
        "job_id": "j1", "mismatch": "too_senior", "reason": "asks 10+ yrs",
        "title": "Senior Staff Engineer", "company": "A", "url": "https://boards/j1",
    }
    # the single batched call is counted against the run budget
    assert out["run_metrics"]["llm_calls"] == 1


def test_node_drops_too_junior_for_a_senior_profile():
    """The seniority axis is bidirectional: a senior profile sheds too-junior roles."""
    result = RelevanceFilterResult(verdicts=[
        RelevanceVerdict(job_id="j2", keep=False, mismatch="too_junior", reason="entry level"),
    ])
    node = make_relevance_filter_node(_FakeAgent(result), MagicMock())
    out = node(_state(effective_config={"search": {"min_years_experience": 8}}))

    assert [j["id"] for j in out["normalized_jobs"]] == ["j1", "j3"]
    assert out["discovery_stats"]["relevance_drops"][0]["mismatch"] == "too_junior"


def test_node_redacts_profile_into_agent_context():
    """The profile must reach the agent only through the redaction seam (ADR-069):
    raw_text dropped, name replaced."""
    agent = _FakeAgent(RelevanceFilterResult(verdicts=[]))
    node = make_relevance_filter_node(agent, MagicMock())
    node(_state(resume_profile={"name": "Jane Doe", "raw_text": "FULL RESUME", "skills": ["Go"]}))

    cached = agent.last_context["_cached"]["resume_profile"]
    assert "raw_text" not in cached
    assert cached["name"] == "[CANDIDATE]"
    assert cached["skills"] == ["Go"]


# ── Node: never lose the run ─────────────────────────────────────────────────

def test_node_keeps_all_on_agent_failure():
    node = make_relevance_filter_node(_FakeAgent(raises=LLMProviderError("boom")), MagicMock())
    out = node(_state())

    assert [j["id"] for j in out["normalized_jobs"]] == ["j1", "j2", "j3"]
    assert "relevance_filter_error" in out["discovery_stats"]
    assert out["errors"] and out["errors"][-1]["error_type"] == "filter_failed"
    # a failed call costs nothing -> no llm_calls increment in this node
    assert "run_metrics" not in out


def test_node_keeps_all_on_empty_verdicts():
    node = make_relevance_filter_node(_FakeAgent(RelevanceFilterResult(verdicts=[])), MagicMock())
    out = node(_state())

    assert len(out["normalized_jobs"]) == 3
    assert out["discovery_stats"]["relevance_dropped"] == 0


def test_node_ignores_verdict_for_unknown_job():
    result = RelevanceFilterResult(verdicts=[
        RelevanceVerdict(job_id="ghost", keep=False, mismatch="unrelated", reason="not real"),
    ])
    node = make_relevance_filter_node(_FakeAgent(result), MagicMock())
    out = node(_state())

    # the invented job cannot drop anything; all three real jobs survive
    assert len(out["normalized_jobs"]) == 3
    assert out["discovery_stats"]["relevance_dropped"] == 0


def test_node_noop_on_empty_input():
    node = make_relevance_filter_node(_FakeAgent(RelevanceFilterResult(verdicts=[])), MagicMock())
    out = node(_state(normalized_jobs=[]))
    assert out.get("normalized_jobs") is None  # node returns only step markers


# ── ADR-094: clearance exclusion folded into the relevance filter ────────────

def _clearance_state(**over):
    jobs = [
        {"id": "c1", "title": "Software Engineer", "company": "Gov",
         "job_description": "Active TS/SCI clearance required.", "url": "https://boards/c1"},
        {"id": "c2", "title": "Software Engineer", "company": "Acme",
         "job_description": "build things, no clearance needed", "url": "https://boards/c2"},
    ]
    s = _state(normalized_jobs=jobs, effective_config={
        "search": {"relevance_filter": True, "exclude_clearance": True}})
    s.update(over)
    return s


def test_clearance_dropped_deterministically_before_llm():
    # The agent keeps everything it is shown; the cleared job must already be gone
    # AND never sent to the agent (no token spend).
    agent = _FakeAgent(RelevanceFilterResult(verdicts=[]))
    node = make_relevance_filter_node(agent, MagicMock())
    out = node(_clearance_state())
    assert [j["id"] for j in out["normalized_jobs"]] == ["c2"]
    assert [j["job_id"] for j in agent.last_context["jobs"]] == ["c2"]  # c1 not sent
    stats = out["discovery_stats"]
    assert stats["clearance_dropped"] == 1
    _drop = next(d for d in stats["relevance_drops"] if d["mismatch"] == "requires_clearance")
    # clearance drops also carry title + company + url for the "why filtered out"
    # panel (the link lets the user verify the clearance requirement themselves)
    assert _drop["title"] == "Software Engineer"
    assert _drop["company"] == "Gov"
    assert _drop["url"] == "https://boards/c1"


def test_clearance_kept_when_flag_off():
    agent = _FakeAgent(RelevanceFilterResult(verdicts=[]))
    node = make_relevance_filter_node(agent, MagicMock())
    out = node(_clearance_state(effective_config={
        "search": {"relevance_filter": True, "exclude_clearance": False}}))
    assert {j["id"] for j in out["normalized_jobs"]} == {"c1", "c2"}
    assert {j["job_id"] for j in agent.last_context["jobs"]} == {"c1", "c2"}
    assert out["discovery_stats"].get("clearance_dropped", 0) == 0


def test_clearance_all_dropped_skips_the_llm_call():
    jobs = [{"id": "c1", "title": "Eng", "company": "Gov",
             "job_description": "Top Secret clearance with polygraph required"}]
    agent = _FakeAgent(RelevanceFilterResult(verdicts=[]))
    node = make_relevance_filter_node(agent, MagicMock())
    out = node(_clearance_state(normalized_jobs=jobs))
    assert out["normalized_jobs"] == []
    assert out["discovery_stats"]["clearance_dropped"] == 1
    assert not hasattr(agent, "last_context")  # agent never called (no candidates)


# ── Router: three-way precedence ─────────────────────────────────────────────

def test_gate_defaults_to_score_jobs():
    assert scoring_mode_gate({"effective_config": {}}) == "score_jobs"


def test_gate_routes_to_relevance_filter_when_enabled():
    state = {"effective_config": {"search": {"relevance_filter": True}}}
    assert scoring_mode_gate(state) == "relevance_filter"


def test_gate_manual_selection_wins_over_relevance_filter():
    state = {"effective_config": {
        "search": {"relevance_filter": True},
        "scoring": {"manual_selection": True},
    }}
    assert scoring_mode_gate(state) == "await_scoring_selection"


# ── Limits: config read + discovery widening ─────────────────────────────────

def test_get_relevance_filter_reads_config():
    assert get_relevance_filter({"effective_config": {"search": {"relevance_filter": True}}}) is True
    assert get_relevance_filter({"effective_config": {"search": {}}}) is False
    assert get_relevance_filter({}) is False


def test_relevance_filter_widens_discovery_net():
    on = {"effective_config": {"search": {"relevance_filter": True}, "scoring": {"max_scored": 10}}}
    assert get_max_discovered_jobs(on) == MAX_DISCOVERED_JOBS


def test_plain_auto_mode_caps_discovery_at_scored():
    off = {"effective_config": {"scoring": {"max_scored": 7}}}
    assert get_max_discovered_jobs(off) == 7


# ── Agent: end-to-end through BaseAgent._run ─────────────────────────────────

def test_agent_returns_validated_result():
    class _Provider:
        provider_name = "claude"
        model_name = "claude-haiku-4-5-20251001"

        def complete_with_usage(self, agent_name, context, schema):
            return (
                {"verdicts": [
                    {"job_id": "j1", "keep": False, "mismatch": "unrelated", "reason": "sales role"},
                ]},
                LLMUsage(tokens_input=150, tokens_output=10, cost_usd=0.0002),
            )

    obs = MagicMock()
    obs.log_agent_started.return_value = "evt-1"
    agent = RelevanceFilterAgent(_Provider(), obs)

    res = agent.run("wf1", {"jobs": [{"job_id": "j1", "title": "Account Executive"}]})
    assert isinstance(res, RelevanceFilterResult)
    assert res.verdicts[0].job_id == "j1"
    assert res.verdicts[0].keep is False
    assert res.verdicts[0].mismatch == "unrelated"


# ── Prompt calibration: suitability/adjacency, not keyword role-match ─────────

def test_prompt_judges_suitability_and_adjacency_not_keyword_match():
    """Forcing function: the relevance prompt must judge candidate SUITABILITY
    (keeping adjacent, transferable-skill roles by career stage), not a strict
    keyword match to target_roles. Guards against a regression to the old binary
    'in-domain or drop' rule that stranded entry candidates open to adjacent work.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2]
              / "app" / "prompts" / "agents" / "relevance_filter.txt").read_text(encoding="utf-8")
    low = prompt.lower()
    # The suitability/adjacency calibration must be present...
    assert "suitab" in low
    assert "adjacent" in low
    assert "transferable" in low
    assert "career stage" in low or "early-career" in low
    # ...and target_roles must be explicitly NOT a hard boundary.
    assert "not a hard boundary" in low or "not merely" in low
    # Prompt version was bumped so the change is observable in llm_calls.
    assert prompt.lstrip().startswith("# version: 4")


def test_prompt_strict_seniority_on_truncated_text_for_early_career():
    """Forcing function (ADR-104): the relevance prompt must (a) allow world-knowledge
    of a role's TYPICAL seniority on the SENIORITY axis when the text is truncated/
    silent, and (b) prefer PRECISION (drop too_senior on ambiguity) for an
    early-career candidate. Guards the over-experienced-jobs leak (Adzuna 500-char
    snippets hide the years requirement). Must stay field-agnostic.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2]
              / "app" / "prompts" / "agents" / "relevance_filter.txt").read_text(encoding="utf-8")
    low = prompt.lower()
    # (a) truncated-text world-knowledge on the seniority axis.
    assert "truncat" in low
    assert "typical" in low
    # (b) precision over recall for early-career seniority.
    assert "precision" in low and "early-career" in low
    # Still field-agnostic (no industry assumption baked into the new rule).
    assert "do not assume any particular industry" in low


def test_prompt_is_field_agnostic_not_hardcoded_per_profile():
    """Forcing function (sustainability): adjacency must be DERIVED from the
    candidate's own profile, not hardcoded per industry in the shared prompt. The
    prompt must instruct deriving the field from resume_profile and must NOT assume
    any particular industry - otherwise it does not scale to a large/enterprise user
    base. The cybersecurity case may appear ONLY as a labelled example.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[2]
              / "app" / "prompts" / "agents" / "relevance_filter.txt").read_text(encoding="utf-8")
    low = prompt.lower()
    # Adjacency is derived from the per-profile data, generically.
    assert "derive" in low and "resume_profile" in low
    assert "do not assume" in low  # field-agnostic guard, e.g. "do not assume any particular industry"
    # The generic drop test is "a clearly different profession", not "not cyber".
    assert "different profession" in low
    # Cybersecurity, if mentioned, must be flagged as illustrative only.
    if "cybersecurity" in low:
        assert "illustrative" in low or "example" in low
