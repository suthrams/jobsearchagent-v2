"""Read-only helpers for browse views — query data/v2.db directly.

These functions are used by the Streamlit UI for all read-only views (scored jobs
table, deep review results, companies chart, run history). They do NOT call FastAPI.
Write actions (start workflow, HITL decisions) always go through api_client.py.

All tables store complex data as JSON blobs. Individual fields are extracted with
SQLite's json_extract(). The main job table is 'jobs', not 'job_postings'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("data/v2.db")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


# ── ADR-062: per-user read scoping ───────────────────────────────────────────
# History and cross-run analytics show only the active profile's data. Scoping is
# cooperative (Decision E): it filters which rows a view reads, it is not an
# authorization boundary. user_id=None means "no filter" (legacy / tests).
#
# Ownership of every per-run row falls out of workflow_runs.user_id via the
# workflow_run_id foreign key. Pre-existing rows were backfilled to '0' by the
# init_db migration; rows with no workflow_runs row (very old data) COALESCE to
# '0' so they remain visible under the default profile and stay hidden from new
# profiles. The string form ("0", "1", ...) matches how the identity seam resolves.


@st.cache_data(ttl=30)
def load_user_resumes(user_id: str) -> pd.DataFrame:
    """A profile's resumes, newest first — backs the Start New Run resume picker.

    The active resume (is_active=1) is flagged so the picker can default to it.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT id            AS resume_id,
                   file_name,
                   COALESCE(is_active, 0) AS is_active,
                   version,
                   created_at
            FROM resumes
            WHERE COALESCE(user_id, '0') = ?
            ORDER BY is_active DESC, created_at DESC
            """,
            conn,
            params=(str(user_id),),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_user_clinic_reviews(user_id: str) -> pd.DataFrame:
    """A profile's resume_clinic_reviews, newest first — backs the Resume Clinic
    past-runs panel (ADR-066). One row per clinic run; JSON blobs are returned as
    strings for compact display, parsed on demand by the view."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT id            AS clinic_id,
                   resume_id,
                   target_role,
                   target_track,
                   seniority_aware,
                   decision,
                   decided_at,
                   created_at
            FROM resume_clinic_reviews
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            conn,
            params=(str(user_id),),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_scored_jobs(include_excluded: bool = False,
                     user_id: str | None = None) -> pd.DataFrame:
    """All scored jobs joined with posting metadata. Extracts scores from score_json blob.

    ADR-057: rows with `jobs.excluded = 1` are filtered out by default.
    Pass include_excluded=True to surface them (the "Include excluded jobs"
    sidebar toggle).

    ADR-062: when user_id is given, only jobs scored by that profile's runs are
    returned (via the workflow_runs owner of each score). None = all profiles.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    clauses = []
    params: list = []
    if not include_excluded:
        clauses.append("(j.excluded = 0 OR j.excluded IS NULL)")
    if user_id is not None:
        clauses.append("COALESCE(wr.user_id, '0') = ?")
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _connect()
    try:
        df = pd.read_sql_query(
            f"""
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
            """,
            conn,
            params=tuple(params),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_workflow_runs(user_id: str | None = None) -> pd.DataFrame:
    """Run history derived from job_scores (legacy fallback when workflow_runs is empty).

    ADR-062: when user_id is given, scope to that profile via the workflow_runs
    owner. Scores whose run has no workflow_runs row COALESCE to '0' so legacy
    data stays visible under the default profile.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    where = ""
    params: tuple = ()
    if user_id is not None:
        where = "WHERE COALESCE(wr.user_id, '0') = ?"
        params = (str(user_id),)
    conn = _connect()
    try:
        df = pd.read_sql_query(
            f"""
            SELECT js.workflow_run_id                       AS id,
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
            """,
            conn,
            params=params,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_deep_review_results(workflow_id: str) -> pd.DataFrame:
    """Resume reviews + career advice for a completed workflow. Extracts from JSON blobs."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT rr.job_id,
                   json_extract(rr.review_json, '$.overall_fit_summary')       AS overall_fit_summary,
                   json_extract(rr.review_json, '$.critical_gaps')             AS critical_gaps_json,
                   json_extract(rr.review_json, '$.resume_only_gaps')          AS resume_only_gaps_json,
                   json_extract(rr.review_json, '$.career_gaps_observed')      AS career_gaps_observed_json,
                   json_extract(rr.review_json, '$.suggested_improvements')    AS suggested_improvements_json,
                   json_extract(rr.review_json, '$.confidence')                AS review_confidence,
                   json_extract(ca.advice_json, '$.positioning_summary')       AS positioning_summary,
                   json_extract(ca.advice_json, '$.resume_gaps')               AS resume_gaps_json,
                   json_extract(ca.advice_json, '$.career_gaps')               AS career_gaps_json,
                   json_extract(ca.advice_json, '$.recommended_next_action')   AS recommended_next_action,
                   json_extract(ca.advice_json, '$.confidence')                AS advice_confidence
            FROM resume_reviews rr
            LEFT JOIN career_advice ca
                   ON rr.job_id = ca.job_id
                  AND rr.workflow_run_id = ca.workflow_run_id
            WHERE rr.workflow_run_id = ?
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_interview_prep(workflow_id: str) -> pd.DataFrame:
    """Interview prep results for a completed workflow. Extracts from prep_json blob.

    The json_extract paths must match `app/schemas/interview_prep.py::InterviewPrep`
    field names exactly. A previous version of this query used short paths
    (`$.likely_topics`, `$.weak_areas`, etc.) that did not exist in the schema,
    so every column came back NULL and the UI showed no content despite
    interview_prep rows being persisted. Fixed 2026-05-05; see
    test_db_reader_query_field_names_match_schemas regression test.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT job_id,
                   json_extract(prep_json, '$.likely_interview_topics')      AS likely_topics_json,
                   json_extract(prep_json, '$.technical_topics_to_review')   AS technical_topics_json,
                   json_extract(prep_json, '$.leadership_stories_to_prepare') AS leadership_stories_json,
                   json_extract(prep_json, '$.weak_areas_to_defend')         AS weak_areas_json,
                   json_extract(prep_json, '$.questions_to_ask_interviewer') AS questions_to_ask_json,
                   json_extract(prep_json, '$.seven_day_prep_plan')          AS seven_day_plan_json,
                   json_extract(prep_json, '$.confidence')                   AS confidence
            FROM interview_prep
            WHERE workflow_run_id = ?
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=10)
def load_recent_workflows() -> pd.DataFrame:
    """Recent workflows from LangGraph checkpoints, enriched with job score counts.

    Used for the reconnect UI on the Monitor screen after a server restart.
    checkpoints is the authoritative source because it is written for every
    workflow, even ones that crashed before producing scores.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        checkpoints = pd.read_sql_query(
            """
            SELECT thread_id AS workflow_id, MAX(rowid) AS last_rowid
            FROM checkpoints
            GROUP BY thread_id
            ORDER BY last_rowid DESC
            LIMIT 10
            """,
            conn,
        )
        if checkpoints.empty:
            return pd.DataFrame()
        scores = pd.read_sql_query(
            """
            SELECT workflow_run_id,
                   COUNT(*)     AS jobs_scored,
                   MAX(overall_score) AS best_score,
                   MIN(created_at)    AS started_at
            FROM job_scores
            GROUP BY workflow_run_id
            """,
            conn,
        )
        df = checkpoints.merge(
            scores, left_on="workflow_id", right_on="workflow_run_id", how="left"
        ).drop(columns=["workflow_run_id", "last_rowid"], errors="ignore")
        df["jobs_scored"] = df["jobs_scored"].fillna(0).astype(int)
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


# ── Live-run observability (short TTL — refreshes every 5 s) ─────────────────

@st.cache_data(ttl=5)
def load_step_executions(workflow_id: str) -> pd.DataFrame:
    """Workflow-level step timeline for a run (discover_jobs, score_jobs, etc.)."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT step, status, duration_ms, notes, started_at, completed_at
            FROM step_executions
            WHERE workflow_run_id = ?
            ORDER BY started_at ASC
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=5)
def load_agent_events(workflow_id: str) -> pd.DataFrame:
    """Per-agent-call events (started / completed / failed) for a run."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT agent_name, event_type, status, duration_ms,
                   input_summary, output_summary, created_at
            FROM agent_events
            WHERE workflow_run_id = ?
            ORDER BY created_at ASC
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=10)
def load_workflow_run(workflow_id: str) -> dict | None:
    """Return the persisted workflow_runs row (with state_json parsed) or None."""
    if not DB_PATH.exists():
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE id = ?",
            (workflow_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    import json as _json
    keys = ["id", "workflow_type", "status", "current_step", "state_json",
            "user_id", "resume_id", "selected_job_id",
            "started_at", "updated_at", "completed_at", "error_message"]
    rec = dict(zip(keys, row))
    try:
        rec["state"] = _json.loads(rec.pop("state_json") or "{}")
    except Exception:
        rec["state"] = {}
    return rec


@st.cache_data(ttl=10)
def load_workflow_jobs(workflow_id: str, include_excluded: bool = True) -> pd.DataFrame:
    """All jobs scored for a workflow run, with per-track scores and pipeline pointers.

    ADR-057: per-workflow Find & Score view ALWAYS surfaces excluded rows
    by default (so the user can see what they have excluded for this run
    and act on it). The `excluded` column lets the UI render a 🚫 badge
    and a Show / Hide toggle. Cross-run analytics use load_scored_jobs()
    where the default is the opposite (exclude by default).
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    where = "" if include_excluded else "AND (j.excluded = 0 OR j.excluded IS NULL)"
    conn = _connect()
    try:
        df = pd.read_sql_query(
            f"""
            SELECT j.id                                                 AS job_id,
                   j.title, j.company, j.location, j.url, j.source,
                   j.created_at                                         AS found_at,
                   COALESCE(j.excluded, 0)                              AS excluded,
                   j.excluded_reason,
                   j.excluded_at,
                   js.overall_score,
                   json_extract(js.score_json, '$.technical_score')     AS technical_score,
                   json_extract(js.score_json, '$.architecture_score')  AS architecture_score,
                   json_extract(js.score_json, '$.leadership_score')    AS leadership_score,
                   json_extract(js.score_json, '$.domain_score')        AS domain_score,
                   json_extract(js.score_json, '$.match_summary')       AS match_summary,
                   json_extract(js.score_json, '$.recommended_next_action') AS recommended_next_action,
                   js.created_at                                        AS scored_at,
                   rr.created_at                                        AS reviewed_at,
                   ca.created_at                                        AS advised_at,
                   ip.created_at                                        AS prep_at
            FROM jobs j
            JOIN job_scores js     ON j.id = js.job_id  AND js.workflow_run_id = ?
            LEFT JOIN resume_reviews rr ON j.id = rr.job_id AND rr.workflow_run_id = js.workflow_run_id
            LEFT JOIN career_advice  ca ON j.id = ca.job_id AND ca.workflow_run_id = js.workflow_run_id
            LEFT JOIN interview_prep ip ON j.id = ip.job_id AND ip.workflow_run_id = js.workflow_run_id
            WHERE 1=1 {where}
            ORDER BY js.overall_score DESC
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=10)
def load_persisted_workflow_runs(limit: int = 50,
                                 user_id: str | None = None) -> pd.DataFrame:
    """Workflow runs with status, timestamps, settings used, and pipeline progress counts.

    Pulls roles, locations, threshold, custom-URL count, max_jobs, and the
    normalized/selected/review counts out of state_json so each history row is
    self-describing — the UI never has to do per-row lookups to render progress.

    Cost columns:
      - cost_usd  : truth source = COALESCE(SUM(llm_calls.estimated_cost),
                    state_json estimate). Falls back to the in-memory estimate
                    only for runs that predate the observability fix
                    (CHANGELOG 2026-05-05) and therefore have no llm_calls
                    rows. Newer runs always read from llm_calls.
      - llm_calls : same fallback pattern.
    """
    if not DB_PATH.exists():
        return pd.DataFrame()
    user_where = ""
    user_params: tuple = ()
    if user_id is not None:
        user_where = "WHERE COALESCE(wr.user_id, '0') = ?"
        user_params = (str(user_id),)
    conn = _connect()
    try:
        df = pd.read_sql_query(
            f"""
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
                   -- ADR-061: prefer the scored-jobs cap; fall back to the legacy
                   -- search.max_jobs for runs recorded before the rename.
                   COALESCE(
                     json_extract(wr.state_json, '$.effective_config.scoring.max_scored'),
                     json_extract(wr.state_json, '$.effective_config.search.max_jobs')
                   ) AS max_jobs,
                   json_array_length(json_extract(wr.state_json, '$.custom_urls')) AS custom_url_count,
                   json_array_length(json_extract(wr.state_json, '$.normalized_jobs')) AS normalized_count,
                   json_array_length(json_extract(wr.state_json, '$.selected_jobs'))   AS selected_count,
                   json_array_length(json_extract(wr.state_json, '$.review_rounds'))   AS review_rounds_count,
                   -- Truth source: SUM(llm_calls.estimated_cost). When llm_calls
                   -- has no rows for this workflow, SUM returns NULL and we fall
                   -- back to the legacy state_json estimate (CHANGELOG 2026-05-05).
                   -- COUNT returns 0 not NULL on empty input, so we wrap it in
                   -- NULLIF to let COALESCE proceed to the fallback.
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
            {user_where}
            GROUP BY wr.id
            ORDER BY wr.started_at DESC
            LIMIT ?
            """,
            conn,
            params=(*user_params, limit),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=10)
def load_job_pipeline(workflow_id: str, job_id: str) -> dict:
    """All persisted outputs for one (workflow_id, job_id) pair, each with its created_at.

    Returns a dict with keys: job, score, review_rounds (list), final_review, advice, prep.
    Missing stages are None / []. All timestamps are raw ISO strings — caller formats.
    """
    out: dict = {
        "job": None,
        "score": None,
        "review_rounds": [],
        "final_review": None,
        "advice": None,
        "prep": None,
    }
    if not DB_PATH.exists():
        return out
    import json as _json
    conn = _connect()
    try:
        # Job row
        row = conn.execute(
            "SELECT id, title, company, location, url, source, created_at "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row:
            out["job"] = {
                "id": row[0], "title": row[1], "company": row[2],
                "location": row[3], "url": row[4], "source": row[5],
                "found_at": row[6],
            }

        # Score
        row = conn.execute(
            "SELECT score_json, overall_score, created_at FROM job_scores "
            "WHERE workflow_run_id = ? AND job_id = ?",
            (workflow_id, job_id),
        ).fetchone()
        if row:
            try:
                payload = _json.loads(row[0] or "{}")
            except Exception:
                payload = {}
            payload["overall_score"] = row[1]
            out["score"] = {"data": payload, "created_at": row[2]}

        # Review rounds (chronological)
        rounds = conn.execute(
            "SELECT round_number, critic_output_json, audit_output_json, "
            "audit_score, stop_reason, created_at "
            "FROM review_rounds WHERE workflow_run_id = ? AND job_id = ? "
            "ORDER BY round_number ASC",
            (workflow_id, job_id),
        ).fetchall()
        for r in rounds:
            try:
                critic = _json.loads(r[1] or "{}")
            except Exception:
                critic = {}
            try:
                audit = _json.loads(r[2] or "{}")
            except Exception:
                audit = {}
            out["review_rounds"].append({
                "round_number": r[0],
                "critic": critic,
                "audit": audit,
                "audit_score": r[3],
                "stop_reason": r[4],
                "created_at": r[5],
            })

        # Final resume review
        row = conn.execute(
            "SELECT review_json, created_at FROM resume_reviews "
            "WHERE workflow_run_id = ? AND job_id = ?",
            (workflow_id, job_id),
        ).fetchone()
        if row:
            try:
                payload = _json.loads(row[0] or "{}")
            except Exception:
                payload = {}
            out["final_review"] = {"data": payload, "created_at": row[1]}

        # Career advice
        row = conn.execute(
            "SELECT advice_json, created_at FROM career_advice "
            "WHERE workflow_run_id = ? AND job_id = ?",
            (workflow_id, job_id),
        ).fetchone()
        if row:
            try:
                payload = _json.loads(row[0] or "{}")
            except Exception:
                payload = {}
            out["advice"] = {"data": payload, "created_at": row[1]}

        # Interview prep
        row = conn.execute(
            "SELECT prep_json, created_at FROM interview_prep "
            "WHERE workflow_run_id = ? AND job_id = ?",
            (workflow_id, job_id),
        ).fetchone()
        if row:
            try:
                payload = _json.loads(row[0] or "{}")
            except Exception:
                payload = {}
            out["prep"] = {"data": payload, "created_at": row[1]}

    except Exception:
        pass
    finally:
        conn.close()
    return out


@st.cache_data(ttl=5)
def load_llm_calls(workflow_id: str) -> pd.DataFrame:
    """Per-LLM-call detail (model, tokens, cache split, cost, latency) for a run."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT agent_name, model, tokens_input, tokens_output,
                   COALESCE(cache_creation_tokens, 0) AS cache_creation_tokens,
                   COALESCE(cache_read_tokens, 0)     AS cache_read_tokens,
                   estimated_cost, latency_ms, created_at
            FROM llm_calls
            WHERE workflow_run_id = ?
            ORDER BY created_at ASC
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df
