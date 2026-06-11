"""ADR-100 Phase 2: on-demand rescue-to-score.

Covers the shared `score_one_job` runner (research -> score -> persist) and the
POST /workflows/{wf}/jobs/{job}/score endpoint (idempotency, resume_profile guard,
scored-job surfacing). The in-graph batch path that also uses the runner is covered
by the existing score_jobs node tests.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_deps, get_graph
from app.api.main import app
from app.services.scoring_runner import score_one_job
from app.workflows.workflow_graph import WorkflowDependencies


def _score_obj(**over):
    """A stand-in scoring result whose model_dump() returns a score dict."""
    payload = {"overall_score": 72, "technical_score": 70,
               "architecture_score": None, "leadership_score": None,
               "domain_score": 60}
    payload.update(over)
    obj = MagicMock()
    obj.model_dump.return_value = payload
    return obj


def _research_obj():
    obj = MagicMock()
    obj.model_dump.return_value = {"summary": "researched"}
    return obj


# ── Runner ───────────────────────────────────────────────────────────────────

def test_score_one_job_researches_scores_and_persists():
    research_agent = MagicMock()
    research_agent.run.return_value = _research_obj()
    scoring_agent = MagicMock()
    scoring_agent.run.return_value = _score_obj(overall_score=81)
    score_repo = MagicMock()

    entry, calls, errs, _ti, _to, _cost = score_one_job(
        job={"id": "job-x", "title": "SOC Analyst", "company": "Acme",
             "job_description": "do soc things", "url": "http://x"},
        workflow_id="wf-1", resume_id="r1", resume_profile={"name": "Cand"},
        career_track="all", active_tracks=["ic"],
        research_agent=research_agent, scoring_agent=scoring_agent, score_repo=score_repo,
    )
    assert entry["status"] == "scored"
    assert entry["overall_score"] == 81
    assert calls == 2 and errs == []
    score_repo.create.assert_called_once()  # the JobScore was persisted


# ── Endpoint ─────────────────────────────────────────────────────────────────

def _state(**over):
    s = {
        "workflow_id": "wf-1",
        "resume_id": "r1",
        "resume_profile": {"name": "Cand", "skills": ["Python"]},
        "effective_config": {"scoring": {"career_track": "all"}},
        "normalized_jobs": [{
            "id": "job-x", "title": "SOC Analyst", "company": "Acme",
            "job_description": "monitor alerts", "url": "http://x",
        }],
    }
    s.update(over)
    return s


def _graph(state):
    graph = MagicMock()
    graph.get_state.return_value = SimpleNamespace(values=state, tasks=[], next=None)
    return graph


def _deps(**over):
    defaults = {f.name: MagicMock() for f in dataclasses.fields(WorkflowDependencies)}
    research = MagicMock(); research.run.return_value = _research_obj()
    scoring = MagicMock(); scoring.run.return_value = _score_obj()
    score_repo = MagicMock(); score_repo.get_by_workflow_run.return_value = []
    defaults.update(research_agent=research, scoring_agent=scoring, score_repo=score_repo)
    defaults.update(over)
    return WorkflowDependencies(**defaults)


def _client(graph, deps):
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_deps] = lambda: deps
    return TestClient(app)


def test_score_endpoint_scores_an_unscored_job():
    deps = _deps()
    with _client(_graph(_state()), deps) as c:
        resp = c.post("/workflows/wf-1/jobs/job-x/score")
    assert resp.status_code == 200
    body = resp.json()
    assert body["already_scored"] is False
    assert body["overall_score"] == 72
    deps.score_repo.create.assert_called_once()
    app.dependency_overrides.clear()


def test_score_endpoint_is_idempotent_when_already_scored():
    deps = _deps()
    deps.score_repo.get_by_workflow_run.return_value = [
        {"job_id": "job-x", "overall_score": 88}
    ]
    with _client(_graph(_state()), deps) as c:
        resp = c.post("/workflows/wf-1/jobs/job-x/score")
    assert resp.status_code == 200
    body = resp.json()
    assert body["already_scored"] is True and body["overall_score"] == 88
    deps.scoring_agent.run.assert_not_called()  # no re-spend
    deps.score_repo.create.assert_not_called()
    app.dependency_overrides.clear()


def test_score_endpoint_409_when_no_resume_profile():
    deps = _deps()
    with _client(_graph(_state(resume_profile={})), deps) as c:
        resp = c.post("/workflows/wf-1/jobs/job-x/score")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "resume_profile_missing"
    app.dependency_overrides.clear()
