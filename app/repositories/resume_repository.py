"""Repository for the resumes table — uploaded resumes and their parsed profiles.

ADR-062: resumes are owned per profile (user_id). create() deactivates only the
owning user's prior resumes, so get_active(user_id) returns exactly one resume
per profile. The raw_text-hash cache is also scoped per user, so two profiles
that happen to share identical resume text still get independent rows.
"""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ResumeRepository:
    """Reads and writes resumes, scoped by owning user_id."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, resume_id: str, user_id: str, file_name: str, raw_text: str,
               parsed_profile: dict, version: int = 1,
               raw_text_hash: str | None = None) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            # Mark this user's previous resumes inactive before inserting the new one.
            conn.execute(
                "UPDATE resumes SET is_active = 0 WHERE user_id = ?", (user_id,)
            )
            conn.execute(
                """INSERT INTO resumes
                   (id, user_id, file_name, raw_text, raw_text_hash,
                    parsed_profile_json, version, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (resume_id, user_id, file_name, raw_text, raw_text_hash,
                 json.dumps(parsed_profile), version, now),
            )

    def get_by_id(self, resume_id: str) -> dict | None:
        # resume_id is a globally unique UUID, so no user scoping is needed to
        # fetch one by id. Ownership for listing/active is handled below.
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE id = ?", (resume_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_raw_text_hash(self, user_id: str, raw_text_hash: str) -> dict | None:
        """Return this user's cached resume profile by SHA-256 hash of raw_text,
        or None. Scoped per user so the cache never crosses profiles."""
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE user_id = ? AND raw_text_hash = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, raw_text_hash),
            ).fetchone()
        return dict(row) if row else None

    def get_active(self, user_id: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM resumes WHERE user_id = ? AND is_active = 1 "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_by_user(self, user_id: str) -> list[dict]:
        """All of a profile's resumes, newest first — backs the UI resume picker."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM resumes WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]
