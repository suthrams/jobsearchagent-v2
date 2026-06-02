"""User-scoped read-services (ADR-075 Phase 2). SQL behind the /users read endpoints.

`list_user_resumes` — a profile's resumes (moved from `db_reader.load_user_resumes`).
Bounded per profile, so it is returned whole inside the uniform list envelope
(ADR-075 §B.1: unpaged reads still use {items, total, limit, offset} for shape
consistency). The clinic past-runs list reuses the existing
`GET /users/{id}/resume-clinic` endpoint instead (its repo read is aligned to
exclude tailoring-chat sessions, ADR-072).
"""
from __future__ import annotations

from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH, get_connection
from app.services.reads.paging import page


def list_user_resumes(user_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """A profile's resumes, active first then newest (ADR-062 scoped).

    Returns the {items, total, limit, offset} envelope; unpaged (a profile has
    few resumes), so limit == total and offset == 0.
    """
    if not Path(db_path).exists():
        return page([], 0, 0, 0)
    try:
        with get_connection(db_path) as conn:
            rows = conn.execute(
                """SELECT id            AS resume_id,
                          file_name,
                          COALESCE(is_active, 0) AS is_active,
                          version,
                          created_at
                   FROM resumes
                   WHERE COALESCE(user_id, '0') = ?
                   ORDER BY is_active DESC, created_at DESC""",
                (str(user_id),),
            ).fetchall()
    except Exception:
        return page([], 0, 0, 0)
    items = [dict(r) for r in rows]
    return page(items, len(items), len(items), 0)


def get_resume_profile(resume_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Parsed profile for one resume (ADR-075 Phase 8). Backs the tailoring/clinic
    chat live preview, which renders against the same parsed profile the backend
    saw. Returns {} when absent. The caller's own resume, shown in its own UI."""
    import json
    if not Path(db_path).exists():
        return {}
    try:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT parsed_profile_json FROM resumes WHERE id = ?", (str(resume_id),)
            ).fetchone()
    except Exception:
        return {}
    if not row or not row["parsed_profile_json"]:
        return {}
    try:
        return json.loads(row["parsed_profile_json"])
    except Exception:
        return {}
