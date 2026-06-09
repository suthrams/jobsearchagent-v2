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
               fidelity_review: dict | None,
               source_workflow_run_id: str | None = None,
               job_id: str | None = None) -> None:
        """Create a clinic-review row. ADR-072: a tailoring-chat session passes
        source_workflow_run_id + job_id to anchor it to a scored job in a run;
        a plain clinic leaves both null (the default)."""
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO resume_clinic_reviews
                   (id, user_id, resume_id, workflow_run_id,
                    source_workflow_run_id, job_id,
                    target_role, target_track, seniority_aware,
                    review_json, alignment_json, overhaul_json,
                    fidelity_review_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (clinic_id, user_id, resume_id, workflow_run_id,
                 source_workflow_run_id, job_id,
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
        """Plain-clinic past runs for a profile, newest first.

        Excludes tailoring-chat sessions (job_id NOT NULL, ADR-072) — those belong
        under their scored job (list_by_job), not the job-agnostic clinic panel.
        This matches the UI read it backs; ADR-075 Phase 2 reuses this endpoint for
        the clinic past-runs list, replacing db_reader.load_user_clinic_reviews
        (which already applied this filter).
        """
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM resume_clinic_reviews
                   WHERE user_id = ? AND job_id IS NULL
                   ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    def list_by_job(self, source_workflow_run_id: str, job_id: str) -> list[dict]:
        """ADR-072: tailoring-chat sessions for a scored job in a run, newest first.

        Distinct from list_by_user (which backs the plain-clinic past-runs panel);
        these rows carry source_workflow_run_id + job_id and belong under the job.
        """
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM resume_clinic_reviews
                   WHERE source_workflow_run_id = ? AND job_id = ?
                   ORDER BY created_at DESC""",
                (source_workflow_run_id, job_id),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows if r is not None]

    def set_edited(self, clinic_id: str, edited: dict,
                   fidelity_review: dict | None = None) -> None:
        """ADR-068: persist a chat-revise turn's output to `edited_json` and
        the fidelity verdict to `fidelity_review_json`. The `decision` field
        is NOT changed - chat turns populate edits; the decision is set by
        explicit user action (Save final edit / Reject).
        """
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE resume_clinic_reviews
                   SET edited_json = ?, fidelity_review_json = ?
                   WHERE id = ?""",
                (json.dumps(edited),
                 json.dumps(fidelity_review) if fidelity_review is not None else None,
                 clinic_id),
            )

    def set_fidelity_review(self, clinic_id: str, fidelity_review: dict | None) -> None:
        """ADR-092: persist ONLY the fidelity verdict, leaving edited_json and
        decision untouched. Used by the on-demand fidelity-check endpoint and
        the accept-time gate now that fidelity no longer runs on every chat turn.
        """
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE resume_clinic_reviews
                   SET fidelity_review_json = ?
                   WHERE id = ?""",
                (json.dumps(fidelity_review) if fidelity_review is not None else None,
                 clinic_id),
            )

    def discard_edits(self, clinic_id: str) -> None:
        """ADR-068: revert the chat-edited state. Clears `edited_json`,
        `decision`, and `decided_at` so the renderer falls back to the
        agent's original overhaul. The agent's overhaul_json stays intact.
        """
        with get_connection(self.db_path) as conn:
            conn.execute(
                """UPDATE resume_clinic_reviews
                   SET edited_json = NULL, decision = NULL, decided_at = NULL
                   WHERE id = ?""",
                (clinic_id,),
            )

    def delete_by_resume(self, resume_id: str, user_id: str) -> int:
        """Delete all clinic reviews for a given (resume_id, user_id).

        Used by the cascade when a resume is deleted - the reviews reference
        a resume that no longer exists, so the past-runs panel would render
        broken rows. Scoped by user_id so a cross-user delete attempt no-ops.
        Returns the number of clinic reviews deleted.
        """
        with get_connection(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM resume_clinic_reviews WHERE resume_id = ? AND user_id = ?",
                (resume_id, str(user_id)),
            )
            return cur.rowcount

    def set_decision(self, clinic_id: str, decision: str,
                     edited: dict | None = None) -> None:
        """Persist the user's approve / revise / reject / edit choice.

        For `edit`, the human-authored draft is stored in `edited_json`; the
        agent's original `overhaul_json` is left intact for the audit trail.
        `reject` and `revise` never carry an edited payload.

        edited_json is only overwritten when an explicit `edited` payload is
        supplied. A decision with `edited=None` LEAVES edited_json untouched -
        it must not wipe a chat-revise session's accumulated edits. (Previously
        this nulled edited_json on every decision, so a `Save final edit` whose
        caller passed a stale/empty payload, or an `approve` after chatting,
        silently clobbered the chat-edited overhaul. compose_resume already
        ignores edited_json for `reject`, so there is no need to clear it here.)
        """
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            if edited is not None:
                conn.execute(
                    """UPDATE resume_clinic_reviews
                       SET decision = ?, decided_at = ?, edited_json = ?
                       WHERE id = ?""",
                    (decision, now, json.dumps(edited), clinic_id),
                )
            else:
                conn.execute(
                    """UPDATE resume_clinic_reviews
                       SET decision = ?, decided_at = ?
                       WHERE id = ?""",
                    (decision, now, clinic_id),
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
