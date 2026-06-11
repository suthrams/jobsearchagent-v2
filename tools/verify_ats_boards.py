"""Verify the configured ATS-direct board slugs against the live public APIs.

ADR-097: the curated Greenhouse/Lever batch is point-in-time verified; boards
rename or close over time. Run this periodically to confirm each configured board
still returns jobs, and prune the dead ones from config.

Usage:
    python tools/verify_ats_boards.py                 # check config/config.yaml
    python tools/verify_ats_boards.py --example       # check config/config.example.yaml
    python tools/verify_ats_boards.py stripe figma    # check ad-hoc slugs (both ATSs)

A board is healthy if the API returns HTTP 200 and a non-empty job array:
    Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
    Lever       GET https://api.lever.co/v0/postings/{slug}?mode=json

Exit code is non-zero if any configured board is dead/empty, so this can gate a
maintenance check in CI.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

# ADR-098: the live board check is shared with the Settings verify-on-add flow so
# both judge a board the same way. `app` is importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ats_scrapers import verify_ats_board  # noqa: E402

TIMEOUT = 8.0


def check_greenhouse(token: str) -> int | None:
    return verify_ats_board("greenhouse", token, timeout_s=TIMEOUT)


def check_lever(slug: str) -> int | None:
    return verify_ats_board("lever", slug, timeout_s=TIMEOUT)


def _load_config(example: bool) -> tuple[list[str], list[str]]:
    name = "config.example.yaml" if example else "config.yaml"
    path = Path(__file__).resolve().parents[1] / "config" / name
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scr = (cfg.get("scrapers") or {})
    gh = list((scr.get("greenhouse") or {}).get("companies") or [])
    lv = list((scr.get("lever") or {}).get("companies") or [])
    print(f"Loaded {len(gh)} greenhouse + {len(lv)} lever boards from {name}\n")
    return gh, lv


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    example = "--example" in argv

    if args:  # ad-hoc slugs: try both ATSs
        with ThreadPoolExecutor(max_workers=12) as ex:
            gh_counts = dict(zip(args, ex.map(check_greenhouse, args)))
            lv_counts = dict(zip(args, ex.map(check_lever, args)))
        for s in args:
            g, l = gh_counts[s], lv_counts[s]
            where = (f"greenhouse:{g}" if g else "") + (f" lever:{l}" if l else "")
            print(f"  {s}: {where or 'NO BOARD / EMPTY on either ATS'}")
        return 0

    gh, lv = _load_config(example)
    dead: list[str] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        gh_counts = dict(zip(gh, ex.map(check_greenhouse, gh)))
        lv_counts = dict(zip(lv, ex.map(check_lever, lv)))

    print("GREENHOUSE:")
    for s in gh:
        n = gh_counts[s]
        print(f"  {'OK ' if n else 'DEAD'} {s}: {n if n else 0} jobs")
        if not n:
            dead.append(f"greenhouse:{s}")
    print("\nLEVER:")
    for s in lv:
        n = lv_counts[s]
        print(f"  {'OK ' if n else 'DEAD'} {s}: {n if n else 0} postings")
        if not n:
            dead.append(f"lever:{s}")

    if dead:
        print(f"\n{len(dead)} dead/empty board(s) to prune: {', '.join(dead)}")
        return 1
    print(f"\nAll {len(gh) + len(lv)} configured boards healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
