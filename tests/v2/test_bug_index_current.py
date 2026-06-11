"""Forcing function: bugs/INDEX.md must be regenerated whenever a BUG-*.md RCA
file is added or edited. The index is DERIVED from the files by
tools/gen_bug_index.py (the directory is the source of truth); a stale checked-in
INDEX.md is a build failure so the listing can never silently drift.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_GEN = _ROOT / "tools" / "gen_bug_index.py"
_INDEX = _ROOT / "bugs" / "INDEX.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_bug_index", _GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bug_index_is_current():
    gen = _load_generator()
    expected = gen.render_index().strip()
    actual = _INDEX.read_text(encoding="utf-8").strip() if _INDEX.exists() else ""
    assert actual == expected, (
        "bugs/INDEX.md is stale - run: python tools/gen_bug_index.py"
    )


def test_every_bug_file_appears_in_index():
    gen = _load_generator()
    index = _INDEX.read_text(encoding="utf-8")
    for path in gen._bug_files():
        bug_id = "BUG-" + gen._NUM_RE.search(path.name).group(1)
        assert f"[{bug_id}]({path.name})" in index, f"{bug_id} missing from INDEX.md"
