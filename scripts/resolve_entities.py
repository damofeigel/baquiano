#!/usr/bin/env python3
"""
resolve_entities.py — Entity resolution over the database (step after load_db.py).

Usage:
    python scripts/resolve_entities.py                    # score candidate pairs, merge/link the sure ones
    python scripts/resolve_entities.py --dry-run          # show what would happen, change nothing
    python scripts/resolve_entities.py export             # review queue -> output/review.csv
    python scripts/resolve_entities.py apply review.csv   # apply your decisions from the CSV

Candidates are pairs with similar names (pg_trgm): mentions the loader left
unresolved, and actors that may be duplicates. Each pair is scored on name,
website, description, country, same listing page and type:
  match    -> linked / merged (merged actors keep their row, status 'merged')
  no_match -> recorded, not proposed again
  unsure   -> review queue. Export it, write match or no_match in the
              your_decision column (and optional notes), then apply it.
A human decision is never overridden by a later run.

Needs DATABASE_URL in .env (see .env.example).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import connect  # noqa: E402
from src.db.resolution import apply_review, export_review, resolve_all  # noqa: E402

DEFAULT_REVIEW = PROJECT_ROOT / "output" / "review.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity resolution over the database.")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "export", "apply"])
    parser.add_argument("file", nargs="?", type=Path, help="Review CSV (export: where to write; apply: what to read)")
    parser.add_argument("--by", default=getpass.getuser(), help="Who decided (apply), default: your user name")
    parser.add_argument("--dry-run", action="store_true", help="Run everything, then roll back")
    parser.add_argument("--database-url", help="Overrides DATABASE_URL")
    args = parser.parse_args()

    with connect(args.database_url) as conn:
        if args.command == "run":
            stats = resolve_all(conn)
            print(f"Scored {stats.pairs_scored} pairs: {stats.merged} actors merged, {stats.linked} mentions linked, "
                  f"{stats.new_actors} new actors, {stats.unsure} to review, {stats.no_match} not the same. "
                  f"{stats.relationships} new relationships.")
        elif args.command == "export":
            path = args.file or DEFAULT_REVIEW
            count = export_review(conn, path)
            print(f"{count} pairs to review written to {path}. Fill in your_decision (match / no_match), "
                  f"then: python scripts/resolve_entities.py apply {path}")
        else:
            if not args.file:
                sys.exit("apply needs the review CSV.")
            counts = apply_review(conn, args.file, args.by)
            print(", ".join(f"{v} {k}" for k, v in counts.items()) or "Nothing to apply.")

        queue = conn.execute("SELECT count(*) FROM match_review_queue").fetchone()[0]
        actors = conn.execute("SELECT count(*) FROM active_actors").fetchone()[0]
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

    print(f"{'DRY RUN, nothing saved. ' if args.dry_run else ''}Active actors: {actors}. Pairs waiting for review: {queue}.")


if __name__ == "__main__":
    main()
