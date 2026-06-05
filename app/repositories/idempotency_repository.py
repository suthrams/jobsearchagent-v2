"""Repository for the idempotency_keys table (ADR-082).

The idempotency key is the PRIMARY KEY, so the INSERT is the atomic claim: the
first caller to insert a given key wins and starts the (paid) run; a concurrent or
later caller with the same key hits the PK constraint and replays the stored
response instead of starting a second run.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class IdempotencyRepository:
    """Atomic claim + replay store for idempotent mutating endpoints."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def claim(
        self,
        idempotency_key: str,
        user_id: str,
        endpoint: str,
        request_fingerprint: str,
        workflow_id: str,
        response: dict,
    ) -> tuple[str, dict | None]:
        """Atomically claim a key, or detect a replay / conflict.

        Returns one of:
          ("claimed", None)        -> first use; the caller should run + return response
          ("replay", stored_resp)  -> same key + same fingerprint; return stored_resp,
                                       do NOT start a second run
          ("conflict", None)       -> same key, DIFFERENT fingerprint; caller returns 409

        The INSERT is the lock: a duplicate key raises IntegrityError, which we
        turn into a replay or conflict by reading the existing row.
        """
        now = utcnow_iso()
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO idempotency_keys
                       (idempotency_key, user_id, endpoint, request_fingerprint,
                        workflow_id, response_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        idempotency_key,
                        user_id,
                        endpoint,
                        request_fingerprint,
                        workflow_id,
                        json.dumps(response),
                        now,
                    ),
                )
            return "claimed", None
        except sqlite3.IntegrityError:
            existing = self.get(idempotency_key)
            if existing is None:
                # Extremely unlikely race: the row vanished between the failed
                # insert and this read. Treat as a conflict (safer than double-run).
                return "conflict", None
            if existing.get("request_fingerprint") != request_fingerprint:
                return "conflict", None
            stored = existing.get("response_json")
            try:
                return "replay", json.loads(stored) if stored else None
            except (TypeError, ValueError):
                return "replay", None

    def get(self, idempotency_key: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_keys WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row is not None else None
