"""SqliteSaver factory — LangGraph checkpoint persistence.

Uses the same SQLite database file as the rest of the application.
LangGraph creates its own checkpoint tables (checkpoints, checkpoint_blobs,
checkpoint_writes) automatically; they do not collide with the 18 app tables.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def make_checkpointer(db_path: str | Path = "data/v2.db") -> Generator[SqliteSaver, None, None]:
    """Context manager that yields a SqliteSaver backed by the application database.

    Usage:
        with make_checkpointer() as cp:
            graph = build_graph(deps_with_checkpointer=cp)
    """
    with SqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        yield checkpointer
