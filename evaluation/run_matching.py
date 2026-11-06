#!/usr/bin/env python3
"""
run_matching.py — Run each setting on the labelled pairs and score the answers.

Usage:
    python evaluation/run_matching.py                            # every setting
    python evaluation/run_matching.py --setting "rules only"     # no model server or database needed

The rules are src/db/resolution.score_pair. A setting with "use_model" asks the model
only about the pairs the rules leave unsure, and applies the same checks as the real
judge (src/db/judge.py). Pairs with no label are skipped. A finished pair is skipped too,
so a stopped run continues where it was (--fresh starts over).
Writes evaluation/results/matching.jsonl. Needs no database.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from typing import Any

from common import PAIRS_FILE, RESULTS_DIR, Usage, append_result, applied, load_settings, read_results, wrap_sync_client
from src.core import config
from src.core.matching import normalize_country, website_domain

RESULTS_FILE = RESULTS_DIR / "matching.jsonl"


def profile(pair_side_id: int, name: str, website: str | None, country: str | None, funds: str | None,
            description: str | None) -> Any:
    """One side of a pair, as the rules and the judge read it. Each fund counts as one page."""
    from src.db.resolution import Profile, plain

    domain = website_domain(website)
    fund_list = {f.strip() for f in (funds or "").split(";") if f.strip()}
    return Profile(
        kind="mention", id=pair_side_id, display=name, names=[plain(name)],
        domains={domain} if domain else set(), countries={normalize_country(country)} if country else set(),
        descriptions=[description] if description else [], sources={hash(f) for f in fund_list},
        funds=fund_list, mentions=1, aliases=[],
    )


def load_pairs() -> list[dict[str, str]]:
    if not PAIRS_FILE.exists():
        sys.exit("No pairs file. Run draft_labels.py matching first.")
    with PAIRS_FILE.open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row["label"].strip().lower() in ("match", "no_match")]


def decide(pair: dict[str, str], classify: Any | None) -> tuple[str, str]:
    """(match, no_match or unsure; who decided: rules or model)."""
    from src.db.judge import JUDGE_SCHEMA, build_messages, interpret
    from src.db.resolution import score_pair

    left, right = (profile(i, *(pair[f"{side}_{c}"] for c in ("name", "website", "country", "funds", "description")))
                   for i, side in enumerate(("left", "right")))
    decision, _, features, reason = score_pair(left, right)
    if decision != "unsure" or classify is None:
        return decision, "rules"
    answer = classify(build_messages(left, right, reason), JUDGE_SCHEMA)
    return interpret(answer, left, right, features)[0], "model"


def run_setting(setting: dict[str, Any], pairs: list[dict[str, str]], done: set[tuple[str, str]]) -> None:
    classify, usage = None, Usage()
    if setting.get("use_model"):
        import openai

        from src.core.structured_llm import check_llm, make_llm_classifier

        client = openai.OpenAI(base_url=config.VLLM_BASE_URL, api_key=config.API_KEY, max_retries=1)
        if problem := check_llm(client, config.VLLM_MODEL):
            print(f"SKIPPING '{setting['name']}': {problem}")
            return
        wrap_sync_client(client, setting, usage)
        classify = make_llm_classifier(client, config.VLLM_MODEL, name="match_judgement")
    for pair in pairs:
        if (setting["name"], pair["pair_id"]) in done:
            continue
        before = usage.as_dict()
        started = time.perf_counter()
        row: dict[str, Any] = {"setting": setting["name"], "pair_id": pair["pair_id"], "kind": pair["kind"],
                               "label": pair["label"].strip().lower(),
                               "model": config.VLLM_MODEL if classify else None,
                               "params": {k: v for k, v in setting.items() if k != "api_key"}}
        try:
            row["decision"], row["decided_by"] = decide(pair, classify)
        except Exception as exc:                   # a bad reply counts as no answer, the run goes on
            row.update(decision="unsure", decided_by="model", failed=f"{type(exc).__name__}: {exc}"[:300])
        after = usage.as_dict()
        row.update(seconds=round(time.perf_counter() - started, 2),
                   usage={key: after[key] - before[key] for key in after})
        append_result(RESULTS_FILE, row)
    print(f"{setting['name']}: done", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score entity matching on the labelled pairs.")
    parser.add_argument("--setting", nargs="+", help="Only these settings (names from settings.json)")
    parser.add_argument("--fresh", action="store_true", help="Forget earlier results and start over")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    pairs = load_pairs()
    if not pairs:
        sys.exit(f"No labelled pairs: fill the label column in {PAIRS_FILE}")
    if args.fresh:
        RESULTS_FILE.unlink(missing_ok=True)
    done = {(r["setting"], r["pair_id"]) for r in read_results(RESULTS_FILE) if "failed" not in r}
    print(f"{len(pairs)} labelled pairs")
    for setting in load_settings("matching", args.setting):
        with applied(setting):
            run_setting(setting, pairs, done)
    print(f"Results: {RESULTS_FILE}. Graphs: python evaluation/plot_results.py")


if __name__ == "__main__":
    main()
