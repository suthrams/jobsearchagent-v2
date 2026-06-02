"""Forcing function for the UI read funnel (ADR-075).

As each screen is migrated off the direct-SQLite read path (`db_reader`) onto the
API, it is removed from the allowlist below. A migrated view that regresses to a
direct DB read (re-imports `db_reader` or `sqlite3`) fails this test. When the
funnel completes (Phase 9) the allowlist is empty and `db_reader` is deleted, so
"the UI never touches the DB directly" becomes a build invariant.

Same style as test_ui_undefined_names / the security-event emit-site guard.
"""
from __future__ import annotations

import re
from pathlib import Path

VIEWS_DIR = Path(__file__).resolve().parents[2] / "app" / "ui" / "views"

# Views NOT yet migrated to the API read path (ADR-075). Each is removed as its
# phase lands; empty at Phase 9. Phase 1 migrated `history`, so it is NOT here.
_ALLOWLIST = {
    "workflow_detail",   # Phase 6
    "job_detail",        # Phase 4
    "analytics",         # Phase 3
    "live_monitor",      # Phase 5
}

_DIRECT_DB = re.compile(r"\b(import\s+sqlite3|from\s+app\.ui\.db_reader|import\s+app\.ui\.db_reader|from\s+app\.ui\s+import\s+db_reader)\b")


def _direct_db_imports(path: Path) -> list[str]:
    hits = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if _DIRECT_DB.search(line):
            hits.append(f"{path.name}:{i}: {line.strip()}")
    return hits


def test_migrated_views_have_no_direct_db_access():
    offenders: list[str] = []
    for py in sorted(VIEWS_DIR.glob("*.py")):
        if py.stem in _ALLOWLIST or py.stem == "__init__":
            continue
        offenders.extend(_direct_db_imports(py))
    assert not offenders, (
        "migrated views must read through the API, not the DB directly "
        "(ADR-075). Offending imports:\n" + "\n".join(offenders)
    )


def test_history_is_migrated():
    """Phase 1 proof: the History view no longer imports db_reader."""
    assert not _direct_db_imports(VIEWS_DIR / "history.py")


def test_allowlist_shrinks_to_empty_by_completion():
    """Tripwire: when the last view is migrated, empty the allowlist AND delete
    db_reader. This keeps the allowlist honest (no stale entries)."""
    for stem in _ALLOWLIST:
        assert (VIEWS_DIR / f"{stem}.py").exists(), (
            f"allowlist names {stem!r} but the view is gone — prune the allowlist"
        )
