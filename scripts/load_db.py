#!/usr/bin/env python3
"""
load_db.py — Load portfolio outputs into PostgreSQL (mentions -> actors).

Usage:
    python scripts/load_db.py                      # latest output of each fund in output/
    python scripts/load_db.py output/a.json        # specific files
    python scripts/load_db.py --all                # every portfolio file, not only the latest
    python scripts/load_db.py --dry-run            # show what would happen, change nothing

Each fund and startup becomes a mention, resolved to an actor only when
that's certain (same domain identifier; or nothing similar exists yet). Same
name without a shared identifier goes to the review queue (match_review_queue)
instead of being merged. Each startup gets an 'Investment' relationship from
its fund. Loading a file again adds nothing.

Needs DATABASE_URL in .env (see .env.example) and the migrations applied.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json  # noqa: E402

from merge_portfolios import find_inputs, fund_names, fund_site  # noqa: E402
from src.db.connection import connect  # noqa: E402
from src.db.loader import LoadStats, load_portfolio  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Load portfolio outputs into PostgreSQL.")
    parser.add_argument("files", nargs="*", type=Path, help="Portfolio output files (default: latest per fund in output/)")
    parser.add_argument("--all", action="store_true", help="Every portfolio file, not only the latest per fund")
    parser.add_argument("--dry-run", action="store_true", help="Run everything, then roll back")
    parser.add_argument("--database-url", help="Overrides DATABASE_URL")
    args = parser.parse_args()

    inputs = find_inputs(args.files, args.all)
    if not inputs:
        sys.exit("No portfolio output files found.")
    names = fund_names(inputs)

    total = LoadStats()
    with connect(args.database_url) as conn:
        for path in inputs:
            data = json.loads(path.read_text(encoding="utf-8"))
            stats = load_portfolio(conn, data, fund_site=fund_site(path)[0], fund_name=names[path], run_ref=path.name)
            total.add(stats)
            print(f"{path.name}: {stats.mentions_new} new mentions ({stats.mentions_existing} already loaded), "
                  f"{stats.new_actors} new actors, {stats.linked} linked, {stats.unsure} to review, "
                  f"{stats.relationships} relationships")
        queue = conn.execute("SELECT count(*) FROM match_review_queue").fetchone()[0]
        actors = conn.execute("SELECT count(*) FROM active_actors").fetchone()[0]
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

    print(f"\n{'DRY RUN, nothing saved. ' if args.dry_run else ''}"
          f"Total: {total.mentions_new} new mentions, {total.new_actors} new actors, {total.linked} linked, "
          f"{total.relationships} relationships. Active actors: {actors}. "
          f"Pairs waiting for review: {queue} (SELECT * FROM match_review_queue).")


if __name__ == "__main__":
    main()
