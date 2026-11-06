#!/usr/bin/env python3
"""
load_enrichment.py — Store an enriched file's results on the actors in PostgreSQL.

Usage:
    python scripts/load_enrichment.py                       # latest output/startups_enriched_*.json
    python scripts/load_enrichment.py output/startups_enriched_<time>.json
    python scripts/load_enrichment.py --dry-run             # show what would happen, change nothing

Run it after load_db.py and resolve_entities.py, once the actors exist. Loading
a file again replaces the stored results. Query them with, for example:
    SELECT name, metadata -> 'enrichment' ->> 'tech_area' FROM active_actors
    WHERE metadata -> 'enrichment' ->> 'deep_tech' = 'true';

Needs DATABASE_URL in .env (see .env.example) and the migrations applied.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.connection import connect  # noqa: E402
from src.db.enrichment import apply_enrichment  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "output"


def main() -> None:
    parser = argparse.ArgumentParser(description="Store enrichment results on the actors in PostgreSQL.")
    parser.add_argument("file", nargs="?", type=Path, help="Enriched file (default: latest output/startups_enriched_*.json)")
    parser.add_argument("--dry-run", action="store_true", help="Run everything, then roll back")
    parser.add_argument("--database-url", help="Overrides DATABASE_URL")
    args = parser.parse_args()

    files = sorted(OUTPUT_DIR.glob("startups_enriched_*.json"))
    path = args.file or (files[-1] if files else None)
    if not path or not path.exists():
        sys.exit("No enriched file. Run scripts/enrich_startups.py first.")
    startups = json.loads(path.read_text(encoding="utf-8"))["startups"]

    with connect(args.database_url) as conn:
        stats = apply_enrichment(conn, startups)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

    print(f"{'DRY RUN, nothing saved. ' if args.dry_run else ''}{path.name}: {stats.startups_matched} startups "
          f"stored on {stats.actors_updated} actors, {stats.not_enriched} not enriched, "
          f"{len(stats.not_found)} with no actor.")
    for name in stats.not_found:
        print(f"    no actor: {name}")


if __name__ == "__main__":
    main()
