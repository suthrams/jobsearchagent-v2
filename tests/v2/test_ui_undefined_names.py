"""Undefined-name invariant for the Streamlit UI package (BUG-001 forcing function).

Companion to tests/v2/test_ui_structure.py. The structural tests prove every view
*imports* clean and exposes a callable render(); they do NOT prove the render BODY
only references names that exist. Python binds globals lazily -- a name used inside
a function is not resolved until that line executes -- so a dropped import (e.g. the
`import httpx` lost when a view body was lifted out of the 3.6K-line streamlit_app.py
during the UI refactor) imports fine and only blows up as a NameError when a user
actually reaches the line. The headless smoke test misses it too: the offending
`except httpx.ReadTimeout:` sits behind an `st.button(...)` branch that returns False
without a simulated click, so the except type is never evaluated.

This test closes that gap statically. It uses the stdlib `symtable` module (the same
scope analyzer CPython uses), which models params, comprehensions, nested functions,
and global/nonlocal correctly -- so a free name that is neither a module-level
binding nor a builtin is genuinely undefined, with no false positives. See
bugs/BUG-001-ui-missing-httpx-import.md for the root-cause analysis.
"""
from __future__ import annotations

import builtins
import symtable
from pathlib import Path

_UI_ROOT = Path(__file__).resolve().parents[2] / "app" / "ui"

# Names every module gets for free that symtable still reports as referenced globals.
_MODULE_DUNDERS = {
    "__file__",
    "__name__",
    "__doc__",
    "__builtins__",
    "__loader__",
    "__spec__",
    "__package__",
    "__dict__",
    "__annotations__",
}
_ALLOWED = set(dir(builtins)) | _MODULE_DUNDERS


def _module_bound_names(top: symtable.SymbolTable) -> set[str]:
    """Names bound at module scope: imports, assignments, def/class, for-targets."""
    return {
        s.get_name()
        for s in top.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_parameter()
    }


def _referenced_globals(table: symtable.SymbolTable, acc: set[str]) -> None:
    """Every name referenced as a global (implicit or explicit) anywhere in the tree,
    plus module-scope references that are never bound locally."""
    is_module = table.get_type() == "module"
    for s in table.get_symbols():
        if not s.is_referenced():
            continue
        bound_here = s.is_assigned() or s.is_imported() or s.is_parameter()
        if s.is_global() or (is_module and not bound_here):
            acc.add(s.get_name())
    for child in table.get_children():
        _referenced_globals(child, acc)


def _undefined_names(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    if "import *" in src:
        # Star imports defeat static resolution; they are banned in this package
        # anyway (every UI module uses explicit imports).
        return ["<star-import: banned>"]
    top = symtable.symtable(src, str(path), "exec")
    bound = _module_bound_names(top)
    refs: set[str] = set()
    _referenced_globals(top, refs)
    return sorted(n for n in refs if n not in bound and n not in _ALLOWED)


def test_no_undefined_names_in_ui_package():
    """Forcing function for BUG-001: a view that references a name it never imported
    (or defines) fails the build here, before a user hits the NameError at runtime."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(_UI_ROOT.rglob("*.py")):
        names = _undefined_names(path)
        if names:
            offenders[str(path.relative_to(_UI_ROOT))] = names
    assert not offenders, (
        "Undefined names in app/ui (likely a dropped import -- see "
        "bugs/BUG-001-ui-missing-httpx-import.md):\n"
        + "\n".join(f"  {f}: {ns}" for f, ns in offenders.items())
    )
