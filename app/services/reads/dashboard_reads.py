"""Dashboard / analytics read-services (ADR-075 Phases 3 + 7).

Cross-run aggregations behind the /dashboard endpoints. `list_scored_jobs` moves
the scored-jobs analytics query out of `db_reader.load_scored_jobs` (Phase 3);
the System Dashboard rollups (Phase 7) reuse the existing `system_health` /
`cost_breakdown` services via thin endpoint wrappers, so they are not re-declared
here.

Scored jobs are returned in full (unpaged) inside the §B.1 envelope: the
analytics views aggregate across the whole set client-side (e.g. best score per
company), so paging would break the aggregation. The set is bounded by
runs x MAX_JOBS_PER_RUN.
"""
from __future__ import annotations

from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH, get_connection
from app.services.reads.paging import page

_SCORED_SQL = """
    SELECT j.id                                                   AS job_id,
           j.title, j.company, j.location, j.url, j.source,
           j.created_at                                           AS found_at,
           COALESCE(j.excluded, 0)                                AS excluded,
           j.excluded_reason,
           j.excluded_at,
           js.overall_score,
           json_extract(js.score_json, '$.technical_score')       AS technical_score,
           json_extract(js.score_json, '$.architecture_score')    AS architecture_score,
           json_extract(js.score_json, '$.leadership_score')      AS leadership_score,
           json_extract(js.score_json, '$.domain_score')          AS domain_score,
           json_extract(js.score_json, '$.match_summary')         AS match_summary,
           json_extract(js.score_json, '$.strengths')             AS strengths_json,
           json_extract(js.score_json, '$.gaps')                  AS gaps_json,
           json_extract(js.score_json, '$.recommended_next_action') AS recommended_next_action,
           js.workflow_run_id                                     AS workflow_id
    FROM jobs j
    JOIN job_scores js ON j.id = js.job_id
    LEFT JOIN workflow_runs wr ON wr.id = js.workflow_run_id
    {where}
    ORDER BY js.overall_score DESC
"""


def list_scored_jobs(
    *, user_id: str | None = None, include_excluded: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    """All scored jobs joined with posting metadata (ADR-075 Phase 3).

    ADR-057: excluded jobs hidden unless include_excluded. ADR-062: profile-scoped
    via each score's owning run (COALESCE to '0'). Returns the §B.1 envelope.
    """
    clauses: list[str] = []
    params: list = []
    if not include_excluded:
        clauses.append("(j.excluded = 0 OR j.excluded IS NULL)")
    if user_id is not None:
        clauses.append("COALESCE(wr.user_id, '0') = ?")
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if not Path(db_path).exists():
        return page([], 0, 0, 0)
    try:
        with get_connection(db_path) as conn:
            rows = conn.execute(_SCORED_SQL.format(where=where), tuple(params)).fetchall()
    except Exception:
        return page([], 0, 0, 0)
    items = [dict(r) for r in rows]
    return page(items, len(items), len(items), 0)
