"""Repository for the reports table — final assembled reports per workflow run."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ReportRepository:
    """Reads and writes reports. A run typically produces one report; report_file_path
    points to the exported file on disk (Markdown or DOCX)."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, report_id: str, workflow_run_id: str, report_json: dict | None = None,
               report_markdown: str | None = None, report_file_path: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO reports
                   (id, workflow_run_id, report_json, report_markdown, report_file_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report_id,
                    workflow_run_id,
                    json.dumps(report_json) if report_json else None,
                    report_markdown,
                    report_file_path,
                    now,
                ),
            )

    def get_by_run(self, workflow_run_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE workflow_run_id = ? ORDER BY created_at DESC LIMIT 1",
                (workflow_run_id,),
            ).fetchone()
        return dict(row) if row else None
