"""Repository for the resume_clinic_reviews table — ADR-066 standalone Resume Clinic.

The Resume Clinic is a job-agnostic, profile-scoped resume tool (review + role/track
alignment + reorganization + evidence-bound rewrites). One row per clinic run.
Out-of-graph: the runner persists here directly; no LangGraph involvement.

Decision shape mirrors tailored_resumes (approve | revise | reject | edit). On `edit`
the human-authored draft is stored in `edited_json`; the agent's original `overhaul_json`
is left intact for the audit trail. The Fidelity Reviewer polices the agent's rewrites
only — an `edit` is owner-authored and never re-reviewed (ADR-059).
"""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ResumeClinicRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, clinic_id: str, user_id: str, resume_id: str, *,
               workflow_run_id: str | None,
               target_role: str | None,
               target_track: str | None,
               seniority_aware: bool,
               review: dict,
               alignment: dict | None,
               overhaul: dict,
               fidelity_review: dict | None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO resume_clinic_reviews
                   (id, user_id, resume_id, workflow_run_id,
                    target_role, target_track, seniority_aware,
                    review_json, alignment_json, overhaul_json,
                    fidelity_review_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (clinic_id, user_id, resume_id, workflow_run_id,
                 target_role, target_track, 1 if seniority_aware else 0,
                 json.dumps(review),
                 json.dumps(alignment) if alignment is not None else None,
                 json.dumps(overhaul),
                 json.dumps(fidelity_review) if fidelity_review is not None else None,
                 now),
            )

    def get_by_id(self, clinic_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resume_clinic_reviews WHERE id = ?",
                (clinic_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def list_by_user(self, user_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM resume_clinic_reviews
                   WHERE user_id = ?
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    def set_decision(self, clinic_id: str, decision: str,
                     edited: dict | None = None) -> None:
        """Persist the user's approve / revise / reject / edit choice.

        For `edit`, the human-authored draft is stored in `edited_json`; the
        agent's original `overhaul_json` is left intact for the audit trail.
        `reject` and `revise` never carry an edited payload.
        """
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE resume_clinic_reviews
                   SET decision = ?, decided_at = ?, edited_json = ?
                   WHERE id = ?""",
                (decision, now,
                 json.dumps(edited) if edited is not None else None,
                 clinic_id),
            )

    @staticmethod
    def _row_to_dict(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for column, key in (
            ("review_json", "review"),
            ("alignment_json", "alignment"),
            ("overhaul_json", "overhaul"),
            ("fidelity_review_json", "fidelity_review"),
            ("edited_json", "edited"),
        ):
            raw = d.get(column)
            if raw:
                try:
                    d[key] = json.loads(raw)
                except Exception:
                    d[key] = None
            else:
                d[key] = None
        # Cast seniority_aware to bool for ergonomic downstream use.
        if "seniority_aware" in d:
            d["seniority_aware"] = bool(d["seniority_aware"])
        return d
