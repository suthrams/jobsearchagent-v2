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


def _rows(db_path: Path, sql: str, params: tuple) -> list[dict]:
    """Run a read query and return list[dict], or [] on any error / missing DB."""
    if not Path(db_path).exists():
        return []
    try:
        with get_connection(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def list_workflow_jobs(workflow_id: str, include_excluded: bool = True,
                       db_path: Path = DEFAULT_DB_PATH) -> dict:
    """All scored jobs for a run, with per-track scores + pipeline pointers
    (ADR-075 Phase 4; moved from db_reader.load_workflow_jobs). Unpaged (bounded by
    MAX_JOBS_PER_RUN); §B.1 envelope."""
    where = "" if include_excluded else "AND (j.excluded = 0 OR j.excluded IS NULL)"
    items = _rows(db_path, f"""
        SELECT j.id AS job_id, j.title, j.company, j.location, j.url, j.source,
               j.posted_at, j.created_at AS found_at, COALESCE(j.excluded, 0) AS excluded,
               j.excluded_reason, j.excluded_at, js.overall_score,
               json_extract(js.score_json, '$.technical_score')     AS technical_score,
               json_extract(js.score_json, '$.architecture_score')  AS architecture_score,
               json_extract(js.score_json, '$.leadership_score')    AS leadership_score,
               json_extract(js.score_json, '$.domain_score')        AS domain_score,
               json_extract(js.score_json, '$.match_summary')       AS match_summary,
               json_extract(js.score_json, '$.recommended_next_action') AS recommended_next_action,
               js.created_at AS scored_at, rr.created_at AS reviewed_at,
               ca.created_at AS advised_at, ip.created_at AS prep_at
        FROM jobs j
        JOIN job_scores js ON j.id = js.job_id AND js.workflow_run_id = ?
        LEFT JOIN resume_reviews rr ON j.id = rr.job_id AND rr.workflow_run_id = js.workflow_run_id
        LEFT JOIN career_advice  ca ON j.id = ca.job_id AND ca.workflow_run_id = js.workflow_run_id
        LEFT JOIN interview_prep ip ON j.id = ip.job_id AND ip.workflow_run_id = js.workflow_run_id
        WHERE 1=1 {where}
        ORDER BY js.overall_score DESC
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_deep_review_results(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Resume reviews + career advice for a run (ADR-075 Phase 6; from
    db_reader.load_deep_review_results)."""
    items = _rows(db_path, """
        SELECT rr.job_id,
               json_extract(rr.review_json, '$.overall_fit_summary')    AS overall_fit_summary,
               json_extract(rr.review_json, '$.critical_gaps')          AS critical_gaps_json,
               json_extract(rr.review_json, '$.resume_only_gaps')       AS resume_only_gaps_json,
               json_extract(rr.review_json, '$.career_gaps_observed')   AS career_gaps_observed_json,
               json_extract(rr.review_json, '$.suggested_improvements') AS suggested_improvements_json,
               json_extract(rr.review_json, '$.confidence')             AS review_confidence,
               json_extract(ca.advice_json, '$.positioning_summary')    AS positioning_summary,
               json_extract(ca.advice_json, '$.resume_gaps')            AS resume_gaps_json,
               json_extract(ca.advice_json, '$.career_gaps')            AS career_gaps_json,
               json_extract(ca.advice_json, '$.recommended_next_action') AS recommended_next_action,
               json_extract(ca.advice_json, '$.confidence')             AS advice_confidence
        FROM resume_reviews rr
        LEFT JOIN career_advice ca ON rr.job_id = ca.job_id AND rr.workflow_run_id = ca.workflow_run_id
        WHERE rr.workflow_run_id = ?
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_interview_prep(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Interview prep rows for a run (ADR-075 Phase 6; from db_reader.load_interview_prep)."""
    items = _rows(db_path, """
        SELECT job_id,
               json_extract(prep_json, '$.likely_interview_topics')      AS likely_topics_json,
               json_extract(prep_json, '$.technical_topics_to_review')   AS technical_topics_json,
               json_extract(prep_json, '$.leadership_stories_to_prepare') AS leadership_stories_json,
               json_extract(prep_json, '$.weak_areas_to_defend')         AS weak_areas_json,
               json_extract(prep_json, '$.questions_to_ask_interviewer') AS questions_to_ask_json,
               json_extract(prep_json, '$.seven_day_prep_plan')          AS seven_day_plan_json,
               json_extract(prep_json, '$.confidence')                   AS confidence
        FROM interview_prep WHERE workflow_run_id = ?
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_step_executions(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Step timeline for a run (ADR-075 Phase 5; from db_reader.load_step_executions)."""
    items = _rows(db_path, """
        SELECT step, status, duration_ms, notes, started_at, completed_at
        FROM step_executions WHERE workflow_run_id = ? ORDER BY started_at ASC
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_agent_events(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Per-agent-call events for a run (ADR-075 Phase 5; from db_reader.load_agent_events)."""
    items = _rows(db_path, """
        SELECT agent_name, event_type, status, duration_ms,
               input_summary, output_summary, created_at
        FROM agent_events WHERE workflow_run_id = ? ORDER BY created_at ASC
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_llm_calls(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Per-LLM-call detail for a run (ADR-075 Phase 5; from db_reader.load_llm_calls)."""
    items = _rows(db_path, """
        SELECT agent_name, model, tokens_input, tokens_output,
               COALESCE(cache_creation_tokens, 0) AS cache_creation_tokens,
               COALESCE(cache_read_tokens, 0)     AS cache_read_tokens,
               estimated_cost, latency_ms, created_at
        FROM llm_calls WHERE workflow_run_id = ? ORDER BY created_at ASC
    """, (workflow_id,))
    return page(items, len(items), len(items), 0)


def list_recent_workflows(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Recent runs from LangGraph checkpoints (ADR-075 Phase 5; from
    db_reader.load_recent_workflows) — the monitor reconnect list."""
    if not Path(db_path).exists():
        return page([], 0, 0, 0)
    try:
        with get_connection(db_path) as conn:
            cps = conn.execute("""
                SELECT thread_id AS workflow_id, MAX(rowid) AS last_rowid
                FROM checkpoints GROUP BY thread_id ORDER BY last_rowid DESC LIMIT 10
            """).fetchall()
            scores = {
                r["workflow_run_id"]: r for r in conn.execute("""
                    SELECT workflow_run_id, COUNT(*) AS jobs_scored,
                           MAX(overall_score) AS best_score, MIN(created_at) AS started_at
                    FROM job_scores GROUP BY workflow_run_id
                """).fetchall()
            }
    except Exception:
        return page([], 0, 0, 0)
    items = []
    for cp in cps:
        wid = cp["workflow_id"]
        sc = scores.get(wid)
        items.append({
            "workflow_id": wid,
            "jobs_scored": int(sc["jobs_scored"]) if sc else 0,
            "best_score": sc["best_score"] if sc else None,
            "started_at": sc["started_at"] if sc else None,
        })
    return page(items, len(items), len(items), 0)


def get_job_pipeline(workflow_id: str, job_id: str,
                     db_path: Path = DEFAULT_DB_PATH) -> dict:
    """All persisted outputs for one (run, job) pair (ADR-075 Phase 4; from
    db_reader.load_job_pipeline). Returns the nested dict the Job Detail view
    renders (job/score/review_rounds/final_review/advice/prep)."""
    import json as _json
    out: dict = {"job": None, "score": None, "review_rounds": [],
                 "final_review": None, "advice": None, "prep": None}
    if not Path(db_path).exists():
        return out

    def _blob(row_json: str | None) -> dict:
        try:
            return _json.loads(row_json or "{}")
        except Exception:
            return {}

    try:
        with get_connection(db_path) as conn:
            r = conn.execute("SELECT id, title, company, location, url, source, posted_at, created_at "
                             "FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if r:
                out["job"] = {"id": r["id"], "title": r["title"], "company": r["company"],
                              "location": r["location"], "url": r["url"], "source": r["source"],
                              "posted_at": r["posted_at"],  # ADR-080
                              "found_at": r["created_at"]}
            r = conn.execute("SELECT score_json, overall_score, created_at FROM job_scores "
                             "WHERE workflow_run_id = ? AND job_id = ?", (workflow_id, job_id)).fetchone()
            if r:
                payload = _blob(r["score_json"])
                payload["overall_score"] = r["overall_score"]
                out["score"] = {"data": payload, "created_at": r["created_at"]}
            for rr in conn.execute(
                "SELECT round_number, critic_output_json, audit_output_json, audit_score, "
                "stop_reason, created_at FROM review_rounds WHERE workflow_run_id = ? AND job_id = ? "
                "ORDER BY round_number ASC", (workflow_id, job_id)).fetchall():
                out["review_rounds"].append({
                    "round_number": rr["round_number"], "critic": _blob(rr["critic_output_json"]),
                    "audit": _blob(rr["audit_output_json"]), "audit_score": rr["audit_score"],
                    "stop_reason": rr["stop_reason"], "created_at": rr["created_at"]})
            r = conn.execute("SELECT review_json, created_at FROM resume_reviews "
                             "WHERE workflow_run_id = ? AND job_id = ?", (workflow_id, job_id)).fetchone()
            if r:
                out["final_review"] = {"data": _blob(r["review_json"]), "created_at": r["created_at"]}
            r = conn.execute("SELECT advice_json, created_at FROM career_advice "
                             "WHERE workflow_run_id = ? AND job_id = ?", (workflow_id, job_id)).fetchone()
            if r:
                out["advice"] = {"data": _blob(r["advice_json"]), "created_at": r["created_at"]}
            r = conn.execute("SELECT prep_json, created_at FROM interview_prep "
                             "WHERE workflow_run_id = ? AND job_id = ?", (workflow_id, job_id)).fetchone()
            if r:
                out["prep"] = {"data": _blob(r["prep_json"]), "created_at": r["created_at"]}
    except Exception:
        pass
    return out


def get_workflow_run_detail(workflow_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """The persisted workflow_runs row with state_json parsed (ADR-075 Phase 6;
    from db_reader.load_workflow_run). None if absent."""
    import json as _json
    if not Path(db_path).exists():
        return None
    try:
        with get_connection(db_path) as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (workflow_id,)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    rec = dict(row)
    try:
        rec["state"] = _json.loads(rec.pop("state_json") or "{}")
    except Exception:
        rec["state"] = {}
    return rec


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
