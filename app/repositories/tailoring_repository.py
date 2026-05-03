"""Repository for the tailored_resumes table — Tailoring Agent drafts awaiting approval."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class TailoringRepository:
    """Reads and writes tailored_resumes. approved defaults to 0; set_decision() persists
    the user's choice (approve / revise / reject) and flips approved to 1 on approve."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, tailoring_id: str, workflow_run_id: str, job_id: str,
               resume_id: str, tailored: dict,
               fidelity_review: dict | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tailored_resumes
                   (id, workflow_run_id, job_id, resume_id, tailored_json,
                    fidelity_review_json, approved, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                (tailoring_id, workflow_run_id, job_id, resume_id,
                 json.dumps(tailored),
                 json.dumps(fidelity_review) if fidelity_review is not None else None,
                 now),
            )

    def approve(self, tailoring_id: str) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE tailored_resumes SET approved = 1 WHERE id = ?",
                (tailoring_id,),
            )

    def set_decision(self, tailoring_id: str, decision: str) -> None:
        """Persist the user's approve/revise/reject choice. Flips approved=1 only on approve."""
        approved = 1 if decision == "approve" else 0
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE tailored_resumes
                   SET decision = ?, decided_at = ?, approved = ?
                   WHERE id = ?""",
                (decision, now, approved, tailoring_id),
            )

    def get_by_id(self, tailoring_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tailored_resumes WHERE id = ?",
                (tailoring_id,),
            ).fetchone()
        return self._row_to_dict(row)

    def get_by_run_job(self, workflow_run_id: str, job_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM tailored_resumes
                   WHERE workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_run_id, job_id),
            ).fetchone()
        return self._row_to_dict(row)

    def list_by_workflow(self, workflow_run_id: str) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM tailored_resumes
                   WHERE workflow_run_id = ?
                   ORDER BY created_at DESC""",
                (workflow_run_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    @staticmethod
    def _row_to_dict(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        if d.get("tailored_json"):
            try:
                d["tailored"] = json.loads(d["tailored_json"])
            except Exception:
                d["tailored"] = None
        if d.get("fidelity_review_json"):
            try:
                d["fidelity_review"] = json.loads(d["fidelity_review_json"])
            except Exception:
                d["fidelity_review"] = None
        else:
            d["fidelity_review"] = None
        return d
