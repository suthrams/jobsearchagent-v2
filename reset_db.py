#!/usr/bin/env python
"""reset_db.py — wipe and recreate the v2 SQLite database.

Usage:
    python reset_db.py                # full reset — deletes all data including resume cache
    python reset_db.py --keep-resumes # keeps parsed resumes so next run skips the Claude parse call
    python reset_db.py --dry-run      # show what would be deleted without touching anything
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "v2.db"

# All tables owned by the v2 app (not LangGraph checkpoint tables).
# Order matters for foreign-key integrity even though FK enforcement is off by default in SQLite.
#
# `users` (ADR-062) is deliberately omitted: full_reset deletes the whole DB file
# and init_db re-seeds user 0, while partial_reset (--keep-resumes) must PRESERVE
# users so the kept resumes' owning profiles still exist. Do not add `users` here
# without also exempting it in partial_reset, or kept resumes would be orphaned.
_APP_TABLES = [
    "llm_calls",
    "agent_events",
    "step_executions",
    "security_events",
    "run_metrics",
    "human_decisions",
    "tailored_resumes",
    "resume_clinic_reviews",
    "interview_prep",
    "career_advice",
    "review_rounds",
    "resume_reviews",
    "job_scores",
    "reports",
    "memory_items",
    "workflow_runs",
    "jobs",
    "user_config",
    "resumes",
]

# LangGraph writes its own checkpoint tables into the same file.
_LANGGRAPH_TABLES = ["checkpoints", "writes", "checkpoint_blobs", "checkpoint_migrations"]


def _tables_in_db(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def full_reset(dry_run: bool) -> None:
    """Delete the DB file (and WAL siblings) then recreate the schema."""
    targets = [DB_PATH, DB_PATH.with_suffix(".db-shm"), DB_PATH.with_suffix(".db-wal")]
    deleted = []
    for path in targets:
        if path.exists():
            if not dry_run:
                path.unlink()
            deleted.append(path.name)

    if deleted:
        print(f"{'[dry-run] would delete' if dry_run else 'Deleted'}: {', '.join(deleted)}")
    else:
        print("No DB files found — nothing to delete.")

    if not dry_run:
        sys.path.insert(0, str(PROJECT_ROOT))
        from app.repositories.database import init_db
        init_db(DB_PATH)
        print(f"Schema recreated at {DB_PATH}")


def partial_reset(dry_run: bool) -> None:
    """Truncate all tables except 'resumes' so the resume parse cache is preserved."""
    import sqlite3
    if not DB_PATH.exists():
        print("No DB file found — running full reset instead.")
        full_reset(dry_run)
        return

    conn = sqlite3.connect(str(DB_PATH))
    existing = _tables_in_db(conn)

    to_clear = [
        t for t in _APP_TABLES + _LANGGRAPH_TABLES
        if t != "resumes" and t in existing
    ]

    for table in to_clear:
        if dry_run:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  [dry-run] would DELETE FROM {table}  ({count} rows)")
        else:
            conn.execute(f"DELETE FROM {table}")

    if not dry_run:
        conn.commit()
        resume_count = conn.execute("SELECT COUNT(*) FROM resumes").fetchone()[0]
        print(f"All tables cleared except resumes ({resume_count} cached resume(s) kept).")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the v2 SQLite database.")
    parser.add_argument(
        "--keep-resumes",
        action="store_true",
        help="Keep parsed resumes so the next run skips the Claude parse step.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making any changes.",
    )
    args = parser.parse_args()

    print(f"DB path : {DB_PATH}")
    print(f"Mode    : {'keep-resumes' if args.keep_resumes else 'full reset'}"
          f"{'  [DRY RUN]' if args.dry_run else ''}")
    print()

    if args.keep_resumes:
        partial_reset(dry_run=args.dry_run)
    else:
        full_reset(dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
