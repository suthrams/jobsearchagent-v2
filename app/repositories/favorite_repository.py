"""Repository for the favorite_jobs table - "My favorite jobs" (ADR-090).

A bounded, per-profile working set of jobs the user flags to tailor toward. It is a
FILTER-INPUT (a signal the user gives the system), the positive counterpart of the
ADR-057 exclude filter - NOT application tracking. A favorite stores only a job
reference + a display snapshot + a timestamp; there is no status / applied / pursuing
/ stage / outcome field, by design (a schema forcing-function test guards this).

Bounded at MAX_FAVORITES per profile so it stays a working set, not a saved-jobs
board. Favorites survive a run purge (the snapshot persists) and are removed only
when their owning profile is deleted (ADR-090 retention).
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso

# The per-profile cap (ADR-090). A working set, not a board. Raising it is an
# "ask first" change (the spec's Boundaries section).
MAX_FAVORITES = 25


class FavoritesCapReached(Exception):
    """Raised when a profile already holds MAX_FAVORITES favorites and a NEW job is
    being favorited. Re-favoriting an already-favorited job never trips this."""

    def __init__(self, cap: int = MAX_FAVORITES):
        self.cap = cap
        super().__init__(f"favorites cap reached ({cap})")


class FavoriteRepository:
    """Reads and writes the favorite_jobs table, scoped by profile (user_id stored
    in the decimal-string form, matching the rest of the ADR-062 schema)."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def add(self, user_id: int | str, workflow_id: str, job_id: str,
            title: str | None = None, company: str | None = None) -> dict:
        """Favorite a job for a profile. Idempotent on (user_id, job_id): a repeat
        returns the existing row (and refreshes the snapshot). Enforces the cap for a
        genuinely new job only - raises FavoritesCapReached past MAX_FAVORITES."""
        uid = str(user_id)
        with get_connection(self.db_path) as conn:
            existing = conn.execute(
                "SELECT 1 FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                (uid, job_id),
            ).fetchone()
            if existing is None:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM favorite_jobs WHERE user_id = ?", (uid,)
                ).fetchone()["n"]
                if count >= MAX_FAVORITES:
                    raise FavoritesCapReached()
                conn.execute(
                    "INSERT INTO favorite_jobs "
                    "(user_id, workflow_id, job_id, title, company, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, workflow_id, job_id, title, company, utcnow_iso()),
                )
            else:
                # Refresh the snapshot (title/company/workflow_id may be fresher).
                conn.execute(
                    "UPDATE favorite_jobs SET workflow_id = ?, title = ?, company = ? "
                    "WHERE user_id = ? AND job_id = ?",
                    (workflow_id, title, company, uid, job_id),
                )
            row = conn.execute(
                "SELECT * FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                (uid, job_id),
            ).fetchone()
        return dict(row)

    def remove(self, user_id: int | str, job_id: str) -> None:
        """Un-favorite a job. Idempotent (no error if it was not favorited)."""
        with get_connection(self.db_path) as conn:
            conn.execute(
                "DELETE FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                (str(user_id), job_id),
            )

    def list_for_user(self, user_id: int | str) -> list[dict]:
        """A profile's favorites, newest first."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM favorite_jobs WHERE user_id = ? "
                "ORDER BY created_at DESC, id DESC",
                (str(user_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def favorited_job_ids(self, user_id: int | str) -> set[str]:
        """The set of job_ids this profile has favorited (a batch lookup for the UI
        so a list of rows can be badged without N queries)."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT job_id FROM favorite_jobs WHERE user_id = ?", (str(user_id),)
            ).fetchall()
        return {r["job_id"] for r in rows}

    def count_for_user(self, user_id: int | str) -> int:
        with get_connection(self.db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM favorite_jobs WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()["n"]

    def remove_all_for_user(self, user_id: int | str) -> int:
        """Delete all of a profile's favorites (used when the profile is deleted).
        Returns the number removed."""
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM favorite_jobs WHERE user_id = ?", (str(user_id),)
            )
            return cursor.rowcount
