import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ResumeRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, resume_id: str, file_name: str, raw_text: str,
               parsed_profile: dict, version: int = 1) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            # Mark previous resumes inactive before inserting new one
            conn.execute("UPDATE resumes SET is_active = 0")
            conn.execute(
                """INSERT INTO resumes
                   (id, file_name, raw_text, parsed_profile_json, version, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (resume_id, file_name, raw_text, json.dumps(parsed_profile), version, now),
            )

    def get_by_id(self, resume_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE id = ?", (resume_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_active(self) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
