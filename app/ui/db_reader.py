"""Read-only helpers for browse views — query data/v2.db directly.

These functions are used by the Streamlit UI for all read-only views (scored jobs
table, deep review results, companies chart, run history). They do NOT call FastAPI.
Write actions (start workflow, HITL decisions) always go through api_client.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path("data/v2.db")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


@st.cache_data(ttl=30)
def load_scored_jobs() -> pd.DataFrame:
    """All scored jobs joined with posting metadata."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT jp.job_id, jp.title, jp.company, jp.location, jp.work_mode,
                   jp.url, jp.source, jp.found_at,
                   js.overall_score, js.technical_score, js.architecture_score,
                   js.leadership_score, js.domain_score,
                   js.match_summary, js.strengths_json, js.gaps_json,
                   js.recommended_next_action, js.workflow_id
            FROM job_postings jp
            JOIN job_scores js USING (job_id)
            WHERE jp.status = 'scored'
            ORDER BY js.overall_score DESC
            """,
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_workflow_runs() -> pd.DataFrame:
    """Run history from workflow_runs table."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM workflow_runs ORDER BY created_at ASC", conn
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


@st.cache_data(ttl=30)
def load_deep_review_results(workflow_id: str) -> pd.DataFrame:
    """Resume reviews + career advice for a completed workflow."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT rr.job_id, rr.overall_fit_summary, rr.critical_gaps_json,
                   rr.suggested_improvements_json, rr.confidence AS review_confidence,
                   ca.positioning_summary, ca.resume_gaps_json, ca.career_gaps_json,
                   ca.recommended_next_action, ca.confidence AS advice_confidence
            FROM resume_reviews rr
            LEFT JOIN career_advice ca USING (job_id)
            WHERE rr.workflow_id = ?
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
    """Interview prep results for a completed workflow."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = _connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT job_id, likely_topics_json, technical_topics_json,
                   weak_areas_json, seven_day_plan_json, confidence
            FROM interview_prep
            WHERE workflow_id = ?
            """,
            conn,
            params=(workflow_id,),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df
