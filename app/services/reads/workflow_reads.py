"""Workflow read-services (ADR-075). SQL behind the /workflows read endpoints.

Phase 1: `list_workflow_runs` — the Workflow History query, moved verbatim from
`db_reader.load_persisted_workflow_runs`, plus the §B.1 paging/sorting contract
(total, limit, offset, allowlisted sort) and the legacy job_scores-derived
fallback (formerly `db_reader.load_workflow_runs`) folded in so the view no longer
needs its own fallback branch.
"""
from __future__ import annotations

from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH, get_connection
from app.services.reads.paging import clamp_offset, page, safe_order, safe_sort

# Columns a client may sort the history list by -> the real SQL column. Only these
# strings can reach ORDER BY (injection guard, ADR-075 §B.1).
_SORTABLE: dict[str, str] = {
    "started_at": "wr.started_at",
    "updated_at": "wr.updated_at",
    "status": "wr.status",
    "cost_usd": "cost_usd",
    "jobs_scored": "jobs_scored",
    "best_score": "best_score",
}
_DEFAULT_SORT = "started_at"

_RUN_ROW_SQL = """
    SELECT wr.id                              AS workflow_id,
           wr.workflow_type,
           wr.status,
           wr.current_step,
           wr.started_at,
           wr.updated_at,
           wr.completed_at,
           wr.error_message,
           json_extract(wr.state_json, '$.search_criteria.roles')         AS roles_json,
           json_extract(wr.state_json, '$.search_criteria.locations')     AS locations_json,
           json_extract(wr.state_json, '$.effective_config.scoring.min_match_score') AS threshold,
           COALESCE(
             json_extract(wr.state_json, '$.effective_config.scoring.max_scored'),
             json_extract(wr.state_json, '$.effective_config.search.max_jobs')
           ) AS max_jobs,
           json_array_length(json_extract(wr.state_json, '$.custom_urls')) AS custom_url_count,
           json_array_length(json_extract(wr.state_json, '$.normalized_jobs')) AS normalized_count,
           json_array_length(json_extract(wr.state_json, '$.selected_jobs'))   AS selected_count,
           json_array_length(json_extract(wr.state_json, '$.review_rounds'))   AS review_rounds_count,
           COALESCE(
               (SELECT SUM(estimated_cost) FROM llm_calls lc1
                WHERE lc1.workflow_run_id = wr.id),
               json_extract(wr.state_json, '$.run_metrics.estimated_cost_usd'),
               0
           ) AS cost_usd,
           COALESCE(
               NULLIF((SELECT COUNT(*) FROM llm_calls lc2
                       WHERE lc2.workflow_run_id = wr.id), 0),
               json_extract(wr.state_json, '$.run_metrics.llm_calls'),
               0
           ) AS llm_calls,
           COUNT(js.id)                       AS jobs_scored,
           MAX(js.overall_score)              AS best_score,
           ROUND(AVG(CAST(js.overall_score AS REAL)), 1) AS avg_score
    FROM workflow_runs wr
    LEFT JOIN job_scores js ON js.workflow_run_id = wr.id
    {where}
    GROUP BY wr.id
    ORDER BY {order_col} {order_dir}
    LIMIT ? OFFSET ?
"""

# Legacy fallback: history derived from job_scores when workflow_runs has no rows
# for this profile (formerly db_reader.load_workflow_runs). Mapped to the same row
# shape (workflow_id + nulls) so the response model is uniform.
_LEGACY_SQL = """
    SELECT js.workflow_run_id                       AS workflow_id,
           COUNT(*)                                AS jobs_scored,
           MAX(js.overall_score)                   AS best_score,
           ROUND(AVG(CAST(js.overall_score AS REAL)), 1) AS avg_score,
           MIN(js.created_at)                      AS started_at,
           MAX(js.created_at)                      AS updated_at
    FROM job_scores js
    LEFT JOIN workflow_runs wr ON wr.id = js.workflow_run_id
    {where}
    GROUP BY js.workflow_run_id
    ORDER BY started_at DESC
    LIMIT ? OFFSET ?
"""


def list_workflow_runs(
    *,
    user_id: str | None,
    limit: int,
    offset: int = 0,
    sort: str = _DEFAULT_SORT,
    order: str = "desc",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    """Workflow History page: {items, total, limit, offset} (ADR-075 §B.1).

    Profile-scoped (ADR-062: COALESCE owner to '0'); `sort` is allowlisted; falls
    back to the job_scores-derived legacy rows when no workflow_runs match.
    """
    offset = clamp_offset(offset)
    order_col = _SORTABLE[safe_sort(sort, set(_SORTABLE), _DEFAULT_SORT)]
    order_dir = safe_order(order)

    if not Path(db_path).exists():
        return page([], 0, limit, offset)

    user_where = "WHERE COALESCE(wr.user_id, '0') = ?" if user_id is not None else ""
    user_params: tuple = (str(user_id),) if user_id is not None else ()

    try:
        with get_connection(db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM workflow_runs wr {user_where}", user_params
            ).fetchone()[0]
            if total:
                rows = conn.execute(
                    _RUN_ROW_SQL.format(where=user_where, order_col=order_col,
                                        order_dir=order_dir),
                    (*user_params, int(limit), int(offset)),
                ).fetchall()
                return page([dict(r) for r in rows], total, limit, offset)

            # Legacy fallback (job_scores-derived); count distinct runs for total.
            legacy_total = conn.execute(
                f"SELECT COUNT(DISTINCT js.workflow_run_id) FROM job_scores js "
                f"LEFT JOIN workflow_runs wr ON wr.id = js.workflow_run_id {user_where}",
                user_params,
            ).fetchone()[0]
            if not legacy_total:
                return page([], 0, limit, offset)
            rows = conn.execute(
                _LEGACY_SQL.format(where=user_where),
                (*user_params, int(limit), int(offset)),
            ).fetchall()
            return page([dict(r) for r in rows], legacy_total, limit, offset)
    except Exception:
        return page([], 0, limit, offset)
