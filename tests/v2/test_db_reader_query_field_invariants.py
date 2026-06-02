"""Pin read-service json_extract paths to the actual Pydantic schema field names.

Why this test exists: a bug shipped where the interview-prep query used
'$.likely_topics' instead of the real schema field '$.likely_interview_topics',
so every column came back NULL. This test introspects the schema and the
read-service source so any schema rename or query change that drops field-name
alignment fails the build.

ADR-075 moved these queries out of db_reader into app/services/reads/
(workflow_reads + dashboard_reads); this test follows them there.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.schemas.career_advice import CareerAdvice
from app.schemas.interview_prep import InterviewPrep
from app.schemas.job_score import JobScore
from app.schemas.resume_review import ResumeReview

_APP = Path(__file__).resolve().parents[2] / "app"
_WORKFLOW_READS = _APP / "services" / "reads" / "workflow_reads.py"
_DASHBOARD_READS = _APP / "services" / "reads" / "dashboard_reads.py"


def _func_body(src_path: Path, func_name: str) -> str:
    src = src_path.read_text(encoding="utf-8")
    match = re.compile(rf"def {re.escape(func_name)}\(.*?(?=^def |\Z)",
                       re.MULTILINE | re.DOTALL).search(src)
    assert match, f"Could not find def {func_name} in {src_path.name}"
    return match.group(0)


def _json_paths(src_path: Path, func_name: str) -> set[str]:
    return set(re.findall(r"json_extract\([^,]+,\s*'\$\.([a-zA-Z0-9_]+)'\)",
                          _func_body(src_path, func_name)))


def _json_paths_by_source(src_path: Path, func_name: str) -> dict[str, set[str]]:
    matches = re.findall(
        r"json_extract\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*,\s*'\$\.([a-zA-Z0-9_]+)'\)",
        _func_body(src_path, func_name),
    )
    out: dict[str, set[str]] = {}
    for source, path in matches:
        out.setdefault(source, set()).add(path)
    return out


def _schema_field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields.keys())


def test_interview_prep_paths_match_InterviewPrep_schema():
    """Regression: would have failed under the old '$.likely_topics' query."""
    missing = _json_paths(_WORKFLOW_READS, "list_interview_prep") - _schema_field_names(InterviewPrep)
    assert not missing, f"list_interview_prep json paths not in InterviewPrep: {sorted(missing)}"


def test_deep_review_paths_match_their_source_schemas():
    """Joins resume_reviews + career_advice; check each JSON source separately."""
    by_source = _json_paths_by_source(_WORKFLOW_READS, "list_deep_review_results")
    assert not (by_source.get("rr.review_json", set()) - _schema_field_names(ResumeReview)), \
        "list_deep_review_results rr.review_json paths drift from ResumeReview"
    assert not (by_source.get("ca.advice_json", set()) - _schema_field_names(CareerAdvice)), \
        "list_deep_review_results ca.advice_json paths drift from CareerAdvice"


def test_workflow_jobs_paths_match_JobScore_schema():
    missing = _json_paths(_WORKFLOW_READS, "list_workflow_jobs") - _schema_field_names(JobScore)
    assert not missing, f"list_workflow_jobs json paths not in JobScore: {sorted(missing)}"


def test_scored_jobs_paths_match_JobScore_schema():
    missing = _json_paths(_DASHBOARD_READS, "list_scored_jobs") - _schema_field_names(JobScore)
    assert not missing, f"list_scored_jobs json paths not in JobScore: {sorted(missing)}"
