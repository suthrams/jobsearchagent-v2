"""CLI for the data-retention purge (ADR-070, implementing ADR-040).

Run LOCALLY against your data/v2.db. This is the headless equivalent of the
POST /admin/purge endpoint: it reads the retention windows from your config
(yaml; windows are protected keys) and deletes rows past their window, cascading
a purged workflow run to all its child rows and purging inactive unreferenced
resumes on their own window.

Purge is DESTRUCTIVE and irreversible. It never runs automatically; this script
(or the endpoint) is the only trigger. By default it prints what WOULD be deleted
and asks for confirmation; pass --yes to skip the prompt.

Usage:
    python -m tools.purge_data            # confirm interactively, then purge
    python -m tools.purge_data --yes      # purge without prompting (e.g. cron)
    python -m tools.purge_data --db-path data/v2.db --config-path config/config.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.repositories.database import DEFAULT_DB_PATH, purge_old_data
from app.services.config_service import CONFIG_PATH, ConfigService


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ADR-070 data-retention purge.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH,
                        help=f"SQLite DB to purge (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--config-path", type=Path, default=CONFIG_PATH,
                        help=f"config.yaml with the retention windows (default: {CONFIG_PATH}).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt (for headless / scheduled runs).")
    args = parser.parse_args()

    config = ConfigService(config_path=args.config_path, db_path=args.db_path).get_effective_config()
    windows = config.get("retention", {})
    print(f"Retention purge on {args.db_path}")
    print(f"  windows: {windows or '(defaults)'}")

    if not args.yes:
        reply = input("This permanently deletes rows past their window. Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted; nothing deleted.")
            return 1

    results = purge_old_data(args.db_path, config=config)
    total = sum(results.values())
    print(f"Purged {total} rows:")
    for table, n in sorted(results.items()):
        if n:
            print(f"  {table:<24} {n}")
    if total == 0:
        print("  (nothing past the retention windows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
