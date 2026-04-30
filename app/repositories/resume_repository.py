"""Repository for the resumes table — uploaded resumes and their parsed profiles."""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ResumeRepository:
    """Reads and writes resumes. create() marks all previous resumes inactive so
    get_active() always returns exactly one resume."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, resume_id: str, file_name: str, raw_text: str,
               parsed_profile: dict, version: int = 1,
               raw_text_hash: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            # Mark previous resumes inactive before inserting new one
            conn.execute("UPDATE resumes SET is_active = 0")
            conn.execute(
                """INSERT INTO resumes
                   (id, file_name, raw_text, raw_text_hash,
                    parsed_profile_json, version, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (resume_id, file_name, raw_text, raw_text_hash,
                 json.dumps(parsed_profile), version, now),
            )

    def get_by_id(self, resume_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE id = ?", (resume_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_raw_text_hash(self, raw_text_hash: str) -> dict | None:
        """Return a cached resume profile by SHA-256 hash of raw_text, or None if not found."""
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE raw_text_hash = ? ORDER BY created_at DESC LIMIT 1",
                (raw_text_hash,),
            ).fetchone()
        return dict(row) if row else None

    def get_active(self) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
