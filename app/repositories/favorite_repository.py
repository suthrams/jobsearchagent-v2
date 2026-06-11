"""Repository for the favorite_jobs table - the kind-discriminated SAVED-JOBS store.

A bounded, per-profile working set of jobs the user has set aside. Two kinds share
this one store (ADR-100):
  - ``favorite``      (ADR-090): jobs the user flags to tailor toward - the positive
                       counterpart of the ADR-057 exclude filter.
  - ``review_later``  (ADR-100): jobs the relevance/clearance filter dropped that the
                       user pulled back out of the discard bucket to consider later.

Both are a FILTER-INPUT (a signal the user gives the system), NOT application
tracking: a row stores only a job reference + a display snapshot (title/company/url/
source) + a timestamp; there is no status / applied / pursuing / stage / outcome
field, for ANY kind, by design (a schema forcing-function test guards this).

Each kind is bounded per profile so it stays a working set, not a board. Saved jobs
survive a run purge (the snapshot persists) and are removed only when their owning
profile is deleted (ADR-090 retention). A job is saved in at most one bucket per
profile (UNIQUE(user_id, job_id)); moving it to another kind moves the bucket.
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso

KIND_FAVORITE = "favorite"
KIND_REVIEW_LATER = "review_later"

# Per-kind caps. A working set, not a board. Raising either is an "ask first" change.
MAX_FAVORITES = 25
MAX_REVIEW_LATER = 50
_CAP_BY_KIND = {KIND_FAVORITE: MAX_FAVORITES, KIND_REVIEW_LATER: MAX_REVIEW_LATER}


class FavoritesCapReached(Exception):
    """Raised when a profile already holds the cap for a kind and a NEW job is being
    saved to it. Re-saving an already-saved job never trips this."""

    def __init__(self, cap: int = MAX_FAVORITES, kind: str = KIND_FAVORITE):
        self.cap = cap
        self.kind = kind
        super().__init__(f"{kind} cap reached ({cap})")


class FavoriteRepository:
    """Reads and writes the saved-jobs store, scoped by profile (user_id stored in
    the decimal-string form, matching the rest of the ADR-062 schema) and by kind.
    The default kind is ``favorite`` so every favorites caller is unchanged."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def add(self, user_id: int | str, workflow_id: str, job_id: str,
            title: str | None = None, company: str | None = None, *,
            kind: str = KIND_FAVORITE, url: str | None = None,
            source: str | None = None) -> dict:
        """Save a job for a profile under ``kind``. Idempotent on (user_id, job_id): a
        repeat refreshes the snapshot (and moves the bucket if the kind differs).
        Enforces the per-kind cap for a genuinely new job only - raises
        FavoritesCapReached past the cap."""
        uid = str(user_id)
        cap = _CAP_BY_KIND.get(kind, MAX_FAVORITES)
        with get_connection(self.db_path) as conn:
            existing = conn.execute(
                "SELECT 1 FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                (uid, job_id),
            ).fetchone()
            if existing is None:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM favorite_jobs WHERE user_id = ? AND kind = ?",
                    (uid, kind),
                ).fetchone()["n"]
                if count >= cap:
                    raise FavoritesCapReached(cap, kind)
                conn.execute(
                    "INSERT INTO favorite_jobs "
                    "(user_id, workflow_id, job_id, kind, title, company, url, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (uid, workflow_id, job_id, kind, title, company, url, source, utcnow_iso()),
                )
            else:
                # Refresh the snapshot (and move the bucket if kind changed).
                conn.execute(
                    "UPDATE favorite_jobs SET workflow_id = ?, kind = ?, title = ?, "
                    "company = ?, url = ?, source = ? WHERE user_id = ? AND job_id = ?",
                    (workflow_id, kind, title, company, url, source, uid, job_id),
                )
            row = conn.execute(
                "SELECT * FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                (uid, job_id),
            ).fetchone()
        return dict(row)

    def remove(self, user_id: int | str, job_id: str, *, kind: str | None = None) -> None:
        """Remove a saved job. Idempotent (no error if it was not saved). When ``kind``
        is given the delete is scoped to that bucket, so removing a review-later entry
        cannot delete a favorite of the same job (and vice versa)."""
        with get_connection(self.db_path) as conn:
            if kind is None:
                conn.execute(
                    "DELETE FROM favorite_jobs WHERE user_id = ? AND job_id = ?",
                    (str(user_id), job_id),
                )
            else:
                conn.execute(
                    "DELETE FROM favorite_jobs WHERE user_id = ? AND job_id = ? AND kind = ?",
                    (str(user_id), job_id, kind),
                )

    def list_for_user(self, user_id: int | str, *, kind: str = KIND_FAVORITE) -> list[dict]:
        """A profile's saved jobs of ``kind``, newest first."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM favorite_jobs WHERE user_id = ? AND kind = ? "
                "ORDER BY created_at DESC, id DESC",
                (str(user_id), kind),
            ).fetchall()
        return [dict(r) for r in rows]

    def saved_job_ids(self, user_id: int | str, *, kind: str = KIND_FAVORITE) -> set[str]:
        """The set of job_ids this profile has saved under ``kind`` (a batch lookup so
        a list of rows can be badged without N queries)."""
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT job_id FROM favorite_jobs WHERE user_id = ? AND kind = ?",
                (str(user_id), kind),
            ).fetchall()
        return {r["job_id"] for r in rows}

    # Back-compat alias: favorites callers (Matches/Opportunity stars) read the
    # 'favorite' kind by name.
    def favorited_job_ids(self, user_id: int | str) -> set[str]:
        return self.saved_job_ids(user_id, kind=KIND_FAVORITE)

    def count_for_user(self, user_id: int | str, *, kind: str = KIND_FAVORITE) -> int:
        with get_connection(self.db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM favorite_jobs WHERE user_id = ? AND kind = ?",
                (str(user_id), kind),
            ).fetchone()["n"]

    def remove_all_for_user(self, user_id: int | str) -> int:
        """Delete ALL of a profile's saved jobs, every kind (used when the profile is
        deleted). Returns the number removed."""
        with get_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM favorite_jobs WHERE user_id = ?", (str(user_id),)
            )
            return cursor.rowcount
