"""Paging / sorting helpers for the read-service layer (ADR-075 §B.1).

Centralizes the list contract so every list endpoint behaves the same:
- `clamp_limit` enforces the default + hard ceiling.
- `safe_sort` validates a client sort field against a per-endpoint allowlist
  (never interpolate a raw client string into ORDER BY — injection guard).
- `page` builds the uniform `{items, total, limit, offset}` envelope.
"""
from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT,
                maximum: int = MAX_LIMIT) -> int:
    """Coerce a client `limit` into [1, maximum], defaulting when missing/invalid."""
    try:
        n = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def clamp_offset(offset: int | None) -> int:
    try:
        n = int(offset)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def safe_sort(sort: str | None, allowed: set[str], default: str) -> str:
    """Return `sort` if it is in the allowlist, else `default`. The result is the
    only string that may reach an ORDER BY clause."""
    return sort if (sort in allowed) else default


def safe_order(order: str | None) -> str:
    """Normalize sort direction to 'ASC' or 'DESC' (default DESC)."""
    return "ASC" if str(order).lower() == "asc" else "DESC"


def page(items: list[dict], total: int, limit: int, offset: int) -> dict:
    """The uniform list envelope returned by every list read-service."""
    return {"items": items, "total": int(total), "limit": int(limit),
            "offset": int(offset)}
