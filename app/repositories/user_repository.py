"""Repository for the users table — profile identities (ADR-062).

A profile is just an identity row: id, display name, and an optional human-only
note. Everything the system *uses* per profile (resume, config, memory, history)
lives in its own table keyed by user_id; this table is deliberately minimal.

Id scheme: id 0 is reserved for all pre-existing single-user data (seeded by the
init_db migration). New profiles created here get the next integer (>= 1), since
SQLite assigns an INTEGER PRIMARY KEY as max(id) + 1.
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class UserRepository:
    """Reads and writes the users table. Create is append-only; the id is
    assigned by SQLite (auto-increment from 1, since 0 is pre-seeded)."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, name: str, note: str | None = None) -> int:
        """Insert a new profile and return its assigned integer id."""
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO users (name, note, created_at) VALUES (?, ?, ?)",
                (name, note, now),
            )
            return int(cursor.lastrowid)

    def update(self, user_id: int | str, name: str, note: str | None = None) -> None:
        """Update a profile's display name and note. Identity ids are never changed."""
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET name = ?, note = ? WHERE id = ?",
                (name, note, int(user_id)),
            )

    # Owned working data deleted with a profile (keyed by the decimal-string
    # user_id, ADR-062). workflow_runs / api_requests / idempotency_keys are NOT
    # here on purpose: history + telemetry are preserved (orphaned to "0" by the
    # COALESCE in the read layer), so a profile delete never loses cost/analytics.
    _OWNED_TABLES = (
        "resumes",
        "user_config",
        "memory_items",
        "resume_clinic_reviews",
        "favorite_jobs",
    )

    def delete(self, user_id: int | str) -> dict:
        """Delete a profile and cascade its OWNED working data; preserve history.

        Refuses to delete profile 0 (the reserved pre-existing-data identity) -
        raises ValueError. Returns a dict of per-table deletion counts (including
        ``users``). Deterministic; one transaction.
        """
        uid_int = int(user_id)
        if uid_int == 0:
            raise ValueError("profile 0 (pre-existing data) cannot be deleted")
        uid_str = str(uid_int)
        counts: dict[str, int] = {}
        with get_connection(self.db_path) as conn:
            for table in self._OWNED_TABLES:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (uid_str,)
                )
                counts[table] = cur.rowcount
            cur = conn.execute("DELETE FROM users WHERE id = ?", (uid_int,))
            counts["users"] = cur.rowcount
        return counts

    def list_all(self) -> list[dict]:
        """All profiles, default user (id 0) first, then by id ascending."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY id ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, user_id: int | str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (int(user_id),)
            ).fetchone()
        return dict(row) if row else None

    def exists(self, user_id: int | str) -> bool:
        """Whether a profile id exists. Used by the identity seam to validate
        an incoming user_id before trusting it."""
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE id = ?", (int(user_id),)
            ).fetchone()
        return row is not None
