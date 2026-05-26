"""The single identity seam (ADR-062).

Every "who is the current user?" decision funnels through `get_current_user_id`.
Today it resolves the acting profile from a `user_id` query parameter (no HTTP
headers, per the design), defaulting to the pre-existing-data profile ("0") when
absent. There is NO authentication: the resolved id scopes which data a request
reads and writes, but it is not an enforced access boundary (cooperative
isolation).

Why one function: when authentication is added later, only the body of
`get_current_user_id` changes — it will read the id from an authenticated
session/token instead of the query parameter. Routers, repositories, the
workflow, and read paths all depend on the *resolved* id and are untouched.

Existence validation is injected, not hard-wired, so the seam stays decoupled
from the database and works in mock mode / tests. Real deps call
`set_user_validator(UserRepository(db_path).exists)` at startup; when no
validator is set (mock mode, unit tests), any non-empty id is accepted.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import HTTPException, Query

from app.repositories.database import DEFAULT_USER_ID

# Process-wide existence checker, wired once at startup by the real dependency
# builder. None means "accept any id" (mock mode / tests have no users table).
_user_exists: Optional[Callable[[str], bool]] = None


def set_user_validator(fn: Optional[Callable[[str], bool]]) -> None:
    """Install (or clear, with None) the profile-existence checker the seam uses."""
    global _user_exists
    _user_exists = fn


def get_current_user_id(user_id: Optional[str] = Query(default=None)) -> str:
    """Resolve the acting profile id. Absent/blank -> the default profile ("0").

    Raises 404 only when a validator is installed and positively reports the id
    does not exist — so a typo'd id fails fast in production, while mock mode and
    unit tests (no validator) accept any non-empty id.
    """
    if user_id is None or not str(user_id).strip():
        return DEFAULT_USER_ID
    uid = str(user_id).strip()
    if _user_exists is not None and not _user_exists(uid):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_user",
                "message": f"No profile with id {uid!r}.",
                "user_id": uid,
            },
        )
    return uid
