"""Repository for the user_config table — per-user preference overrides.

Stores only overrides, not full config. Merged with YAML defaults at runtime
by ConfigService. Protected keys are never written here; they live in config.yaml only.
"""
import json
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ConfigRepository:
    """Reads and writes user_config. Upserts on id so repeated saves are idempotent."""
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def upsert(self, config_id: str, user_id: str | None,
               config_key: str, config_value: object) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO user_config
                   (id, user_id, config_key, config_value_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       config_value_json = excluded.config_value_json,
                       updated_at = excluded.updated_at""",
                (config_id, user_id, config_key, json.dumps(config_value), now, now),
            )

    def get_by_user(self, user_id: str | None) -> list[dict]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM user_config WHERE user_id IS ? ORDER BY config_key ASC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_key(self, user_id: str | None, config_key: str) -> dict | None:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM user_config WHERE user_id IS ? AND config_key = ?",
                (user_id, config_key),
            ).fetchone()
        return dict(row) if row else None
