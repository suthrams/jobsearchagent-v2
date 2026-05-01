"""SqliteSaver factory — LangGraph checkpoint persistence.

Uses the same SQLite database file as the rest of the application.
LangGraph creates its own checkpoint tables (checkpoints, checkpoint_blobs,
checkpoint_writes) automatically; they do not collide with the 18 app tables.
"""
from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def make_checkpointer(db_path: str | Path = "data/v2.db") -> SqliteSaver:
    """Return a SqliteSaver connected to the application database."""
    return SqliteSaver.from_conn_string(str(db_path))
