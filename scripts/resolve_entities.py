#!/usr/bin/env python3
"""
resolve_entities.py — Entity resolution over the database (step after load_db.py).

Usage:
    python scripts/resolve_entities.py                    # score candidate pairs, merge/link the sure ones
    python scripts/resolve_entities.py --dry-run          # show what would happen, change nothing
    python scripts/resolve_entities.py judge --limit 20   # ask the LLM about pairs in the review queue
    python scripts/resolve_entities.py export             # review queue -> output/review.csv
    python scripts/resolve_entities.py apply review.csv   # apply your decisions from the CSV

Candidates are pairs with similar names (pg_trgm): mentions the loader left
unresolved, and actors that may be duplicates. Each pair is scored on name,
website, description, country, same listing page and type:
  match    -> linked / merged (merged actors keep their row, status 'merged')
  no_match -> recorded, not proposed again
  unsure   -> review queue. Export it, write match or no_match in the
              your_decision column (and optional notes), then apply it.
judge asks the LLM (vLLM, VLLM_MODEL) about each unsure pair. Its answer is
applied only when confident and backed by quotes that really are in the
records; otherwise the pair stays in review with the model's reasoning.
A later score run never overrides the judge, and nothing overrides a human.

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
from src.db.judge import judge_decision, pending_pairs  # noqa: E402
from src.db.loader import materialize_relationships  # noqa: E402
from src.db.resolution import apply_review, export_review, resolve_all  # noqa: E402

DEFAULT_REVIEW = PROJECT_ROOT / "output" / "review.csv"


def judge(conn, limit: int | None, commit: bool) -> None:
    import openai

    from src.core import config
    from src.core.structured_llm import check_llm, make_llm_classifier

    client = openai.OpenAI(base_url=config.VLLM_BASE_URL, api_key=config.API_KEY, max_retries=1)
    if problem := check_llm(client, config.VLLM_MODEL):
        sys.exit(problem)
    classify = make_llm_classifier(client, config.VLLM_MODEL, name="match_judgement")
    # Errors that will fail every pair the same way (decided pairs are already committed)
    fatal = (openai.APIConnectionError, openai.AuthenticationError, openai.PermissionDeniedError,
             openai.NotFoundError, openai.BadRequestError)

    pairs = pending_pairs(conn, limit)
    print(f"{len(pairs)} pairs for the judge ({config.VLLM_MODEL})")
    counts: dict[str, int] = {}
    for i, decision_id in enumerate(pairs, 1):
        try:
            with conn.transaction():       # a savepoint: a failed pair leaves nothing half-written
                outcome, reason = judge_decision(conn, decision_id, classify, config.VLLM_MODEL)
        except fatal as exc:
            sys.exit(f"[{i}/{len(pairs)}] stopping, the LLM server failed ({type(exc).__name__}: {exc}). "
                     f"Pairs already judged are saved; rerun to continue.")
        except Exception as exc:
            print(f"[{i}/{len(pairs)}] decision {decision_id}: FAILED {type(exc).__name__}: {exc}")
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"[{i}/{len(pairs)}] {outcome}" + (f": {reason}" if reason else ""))
        if commit:
            conn.commit()
    relationships = materialize_relationships(conn)
    print(", ".join(f"{v} {k}" for k, v in counts.items()) or "Nothing to judge.",
          f"{relationships} new relationships.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity resolution over the database.")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "judge", "export", "apply"])
    parser.add_argument("file", nargs="?", type=Path, help="Review CSV (export: where to write; apply: what to read)")
    parser.add_argument("--by", default=getpass.getuser(), help="Who decided (apply), default: your user name")
    parser.add_argument("--limit", type=int, help="judge: at most N pairs this run")
    parser.add_argument("--dry-run", action="store_true", help="Run everything, then roll back")
    parser.add_argument("--database-url", help="Overrides DATABASE_URL")
    args = parser.parse_args()

    with connect(args.database_url) as conn:
        if args.command == "run":
            stats = resolve_all(conn)
            print(f"Scored {stats.pairs_scored} pairs: {stats.merged} actors merged, {stats.linked} mentions linked, "
                  f"{stats.new_actors} new actors, {stats.unsure} to review, {stats.no_match} not the same. "
                  f"{stats.relationships} new relationships.")
        elif args.command == "judge":
            judge(conn, args.limit, commit=not args.dry_run)
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
