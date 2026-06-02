"""Repository for the api_requests table — HTTP request observability (ADR-074 Gap 5).

Append-only. One row per REST request, written by the FastAPI middleware. Stores
the matched route TEMPLATE (e.g. /tailorings/{tailoring_id}) - never the raw path
or query string - so there is no PII and no unbounded cardinality. Scoped per
profile via user_id (the ?user_id= identity seam, ADR-062).
"""
from pathlib import Path

from .database import DEFAULT_DB_PATH, get_connection, utcnow_iso


class ApiRequestRepository:
    """Reads and writes api_requests. Append-only — no update or delete methods."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def create(self, request_id: str, user_id: str, method: str,
               route_template: str, status_code: int, latency_ms: int) -> None:
        now = utcnow_iso()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """INSERT INTO api_requests
                   (id, user_id, method, route_template, status_code,
                    latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (request_id, user_id, method, route_template,
                 status_code, latency_ms, now),
            )

    def list_for_user(
        self, user_id: str | None = None, days: int | None = None
    ) -> list[dict]:
        """System-level read for the System Dashboard API section (ADR-074 Gap 5).

        api_requests carries user_id directly (no join needed); COALESCE null to
        '0' (ADR-062). ``user_id=None`` => all profiles; ``days=None`` => all-time.
        Newest first. Returns [] if the table is absent.
        """
        clauses: list[str] = []
        params: list = []
        if user_id is not None:
            clauses.append("COALESCE(user_id, '0') = ?")
            params.append(str(user_id))
        if days is not None and days > 0:
            clauses.append(f"created_at >= datetime('now', '-{int(days)} days')")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            with get_connection(self.db_path) as conn:
                rows = conn.execute(
                    f"SELECT * FROM api_requests {where} ORDER BY created_at DESC",
                    tuple(params),
                ).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]
