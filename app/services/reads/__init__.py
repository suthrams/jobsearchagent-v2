"""Read-service layer (ADR-075) — the single home for UI read SQL.

As the UI read path is funnelled through the API (ADR-075), each `db_reader`
query moves here as a pure, deterministic, `user_id`-scoped function returning
JSON-native dicts (the list envelope `{items, total, limit, offset}` for lists).
The API routers wrap these; nothing else re-implements a read query. Mirrors the
existing `cost_breakdown` / `system_health` aggregator services.
"""
