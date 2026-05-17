"""Deterministic figure renderer: JSON spec -> HTML/CSS -> PNG.

Series image system. Produces the rich dark/operational look without the
text-fidelity lottery: every label and number is literal HTML from the
spec, so it is always exact and ASCII, and the aspect ratio is locked.

NON-DESTRUCTIVE BY DEFAULT. Renders into blogs/blog_images/_rendered/
so the current published images (incl. the ChatGPT ones you chose to
keep) are never touched. You compare side by side, then promote the
ones you approve.

Run LOCALLY (needs the Chromium you already installed for Playwright):
    pip install playwright
    python -m playwright install chromium

    python tools/render_figures.py                  # render all -> _rendered/
    python tools/render_figures.py article6_05       # one (substring)
    python tools/render_figures.py article6_05 --promote
        # copy the rendered PNG over the canonical name, after backing
        # up the existing one to <id>__prev_backup.png

Compare:  blogs/blog_images/_rendered/<id>.png   (new, deterministic)
   vs     blogs/blog_images/<id>.png             (currently published)

Send the rendered PNGs back for number-by-number verification against
the locked specs before promoting.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RDIR = ROOT / "tools" / "figure_renderer"
SPECS = RDIR / "specs"
OUT = ROOT / "blogs" / "blog_images"
COMPARE = OUT / "_rendered"
TEMPLATE = RDIR / "template.html"
TMP = RDIR / "_render_tmp.html"   # next to theme.css so the link resolves


def _promote(spec_id: str) -> None:
    src = COMPARE / f"{spec_id}.png"
    dst = OUT / f"{spec_id}.png"
    if not src.exists():
        print(f"  promote skipped: {src.name} not rendered yet", file=sys.stderr)
        return
    if dst.exists():
        backup = OUT / f"{spec_id}__prev_backup.png"
        if not backup.exists():            # never clobber the first backup
            shutil.copy2(dst, backup)
            print(f"  backed up current -> {backup.name}")
    shutil.copy2(src, dst)
    print(f"  promoted -> {dst.name}")


def render(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run:\n  pip install playwright\n"
              "  python -m playwright install chromium", file=sys.stderr)
        return 2

    specs = sorted(SPECS.glob("*.json"))
    if args.filter:
        specs = [s for s in specs if args.filter in s.stem]
    if not specs:
        print("No matching specs.", file=sys.stderr)
        return 3

    tpl = TEMPLATE.read_text(encoding="utf-8")
    COMPARE.mkdir(parents=True, exist_ok=True)
    rc = 0
    rendered: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for sp in specs:
            try:
                spec = json.loads(sp.read_text(encoding="utf-8"))
                w, h = int(spec["width"]), int(spec["height"])
                TMP.write_text(tpl.replace("__SPEC_JSON__", json.dumps(spec)),
                               encoding="utf-8")
                page = browser.new_page(viewport={"width": w, "height": h},
                                        device_scale_factor=2)
                page.goto(TMP.as_uri(), wait_until="networkidle")
                page.wait_for_timeout(300)
                dest = COMPARE / f"{spec['id']}.png"
                page.screenshot(path=str(dest))
                page.close()
                ratio = w / h
                flag = "" if 3.0 <= ratio <= 5.0 else "  (ratio outside 3-5:1)"
                print(f"OK  _rendered/{dest.name}  {w}x{h} @2x  "
                      f"{ratio:.2f}:1{flag}")
                rendered.append(spec["id"])
            except Exception as e:  # noqa: BLE001
                rc = 1
                print(f"FAIL {sp.name}: {e}", file=sys.stderr)
        browser.close()

    if TMP.exists():
        TMP.unlink()

    if args.promote:
        print("\nPromoting (current images backed up to *__prev_backup.png):")
        for sid in rendered:
            _promote(sid)
    else:
        print("\nNON-DESTRUCTIVE: published images untouched. Compare "
              "blogs/blog_images/_rendered/<id>.png vs blogs/blog_images/"
              "<id>.png, send the rendered ones back for verification, then "
              "re-run with --promote.")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Deterministic figure renderer")
    ap.add_argument("filter", nargs="?", default=None,
                    help="substring of the spec stem to render (default: all)")
    ap.add_argument("--promote", action="store_true",
                    help="copy rendered PNGs over the canonical names "
                         "(backs up the current ones first)")
    raise SystemExit(render(ap.parse_args()))
