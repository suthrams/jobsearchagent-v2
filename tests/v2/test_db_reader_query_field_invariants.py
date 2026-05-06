"""Pin db_reader json_extract paths to the actual Pydantic schema field names.

Why this test exists: a bug shipped where db_reader.load_interview_prep used
json_extract paths like '$.likely_topics' that didn't exist in the schema
(the field is 'likely_interview_topics'). Every column came back NULL and
the UI showed no interview prep content despite rows being persisted. The
field name drift went unnoticed for weeks.

This test introspects the actual schema and the db_reader source to ensure
every json_extract path corresponds to a real schema field. If a future
schema rename or db_reader query change drops field-name alignment, this
test fails immediately.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.career_advice import CareerAdvice
from app.schemas.interview_prep import InterviewPrep
from app.schemas.job_score import JobScore
from app.schemas.resume_review import ResumeReview


_DB_READER_PATH = Path(__file__).resolve().parents[2] / "app" / "ui" / "db_reader.py"


def _extract_json_paths_from_function(func_name: str) -> set[str]:
    """All json_extract paths used inside `def func_name(...)`."""
    src = _DB_READER_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"def {re.escape(func_name)}\(.*?(?=^def |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(src)
    assert match, f"Could not find def {func_name} in db_reader.py"
    body = match.group(0)
    return set(re.findall(r"json_extract\([^,]+,\s*'\$\.([a-zA-Z0-9_]+)'\)", body))


def _extract_json_paths_by_source(func_name: str) -> dict[str, set[str]]:
    """Group json_extract paths by the SQL alias of the column being read.
    Useful for queries that join multiple JSON-bearing tables."""
    src = _DB_READER_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"def {re.escape(func_name)}\(.*?(?=^def |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(src)
    assert match, f"Could not find def {func_name} in db_reader.py"
    body = match.group(0)
    # Match: json_extract(<source>, '$.<path>')
    matches = re.findall(
        r"json_extract\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*,\s*'\$\.([a-zA-Z0-9_]+)'\)",
        body,
    )
    out: dict[str, set[str]] = {}
    for source, path in matches:
        out.setdefault(source, set()).add(path)
    return out


def _schema_field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields.keys())


# ── Per-helper assertions ────────────────────────────────────────────────────

def test_load_interview_prep_paths_match_InterviewPrep_schema():
    """Regression: this test would have failed under the old query that used
    '$.likely_topics' instead of '$.likely_interview_topics'."""
    paths = _extract_json_paths_from_function("load_interview_prep")
    schema_fields = _schema_field_names(InterviewPrep)
    missing = paths - schema_fields
    assert not missing, (
        f"load_interview_prep references json paths that don't exist in InterviewPrep:\n"
        f"  drift: {sorted(missing)}\n"
        f"  schema: {sorted(schema_fields)}"
    )


def test_load_deep_review_results_paths_match_their_source_schemas():
    """This query joins resume_reviews + career_advice and pulls fields from
    BOTH schemas. Group paths by the SQL alias and check each group separately."""
    by_source = _extract_json_paths_by_source("load_deep_review_results")
    review_fields = _schema_field_names(ResumeReview)
    advice_fields = _schema_field_names(CareerAdvice)

    # rr.review_json -> ResumeReview
    review_drift = by_source.get("rr.review_json", set()) - review_fields
    assert not review_drift, (
        f"load_deep_review_results references rr.review_json paths not in ResumeReview:\n"
        f"  drift: {sorted(review_drift)}\n"
        f"  schema: {sorted(review_fields)}"
    )
    # ca.advice_json -> CareerAdvice
    advice_drift = by_source.get("ca.advice_json", set()) - advice_fields
    assert not advice_drift, (
        f"load_deep_review_results references ca.advice_json paths not in CareerAdvice:\n"
        f"  drift: {sorted(advice_drift)}\n"
        f"  schema: {sorted(advice_fields)}"
    )


def test_load_workflow_jobs_paths_match_JobScore_schema():
    paths = _extract_json_paths_from_function("load_workflow_jobs")
    schema_fields = _schema_field_names(JobScore)
    missing = paths - schema_fields
    assert not missing, (
        f"load_workflow_jobs references json paths not in JobScore:\n"
        f"  drift: {sorted(missing)}\n"
        f"  schema: {sorted(schema_fields)}"
    )


def test_load_scored_jobs_paths_match_JobScore_schema():
    paths = _extract_json_paths_from_function("load_scored_jobs")
    schema_fields = _schema_field_names(JobScore)
    missing = paths - schema_fields
    assert not missing, (
        f"load_scored_jobs references json paths not in JobScore:\n"
        f"  drift: {sorted(missing)}\n"
        f"  schema: {sorted(schema_fields)}"
    )
