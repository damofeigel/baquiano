#!/usr/bin/env python3
"""
draft_labels.py — Write first drafts of the answer keys, for a person to correct.

Usage:
    python evaluation/draft_labels.py detection     # datasets/detection/<fund>/gold.csv
    python evaluation/draft_labels.py matching      # datasets/matching/pairs.csv

A draft comes from the system being tested, so it can't count as the truth:

  gold.csv    keep = "?" for every row. Set yes or no, add any company the draft missed,
              then set "reviewed": true in the fund's meta.json.
  pairs.csv   label is empty. Fill match or no_match, or leave it empty to skip the pair.
              suggested shows the rules' answer, only to save reading time.

A file that exists is never overwritten (--force does), so corrections are safe.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from pathlib import Path

from common import DETECTION_DIR, PAIRS_FILE, PROJECT_ROOT, same_name

GOLD_COLUMNS = ["keep", "name", "website", "found_by", "note"]
PAIR_SIDE = ["name", "website", "country", "funds", "description"]
PAIR_COLUMNS = ["pair_id", "label", "suggested", "score", "kind",
                *[f"left_{c}" for c in PAIR_SIDE], *[f"right_{c}" for c in PAIR_SIDE], "note"]
# Their last crawl saved filter labels, not companies, so their files make no useful pairs
SKIP_OUTPUTS = ("500.co_", "sosv.com_")
SAMPLES = {"same website": 15, "unsure": 90, "rejected": 45}


def _last_run_output(fund_id: str, url: str) -> list[dict]:
    """The fund's newest portfolio output in output/, if any."""
    from src.core.matching import website_domain

    from merge_portfolios import clean_portfolio

    domain = website_domain(url) or ""
    files = sorted((PROJECT_ROOT / "output").glob(f"{domain}_*.json"))
    if not files or files[-1].name.startswith(SKIP_OUTPUTS):
        return []
    return clean_portfolio(files[-1], json.loads(files[-1].read_text(encoding="utf-8")))[0]


def draft_detection(force: bool) -> None:
    from merge_portfolios import _without_fund_name
    from src.custom_agents.portfolio_agent.heuristics import extract_companies

    for fund_dir in sorted(p for p in DETECTION_DIR.iterdir() if (p / "meta.json").exists()):
        out = fund_dir / "gold.csv"
        if out.exists() and not force:
            print(f"{fund_dir.name}: keeping the existing gold.csv")
            continue
        meta = json.loads((fund_dir / "meta.json").read_text(encoding="utf-8"))
        html = (fund_dir / "pages" / "portfolio.html").read_text(encoding="utf-8")
        rows: list[dict] = [{"name": _without_fund_name(c.name, meta.get("fund_name")) or c.name,
                             "website": c.website or "", "found_by": "parser"}
                            for c in extract_companies(html, meta["url"])]
        text = re.sub(r"\s+", " ", html).casefold()
        for company in _last_run_output(fund_dir.name, meta["url"]):
            name = company.get("name") or ""
            match = next((r for r in rows if same_name(r["name"], name)), None)
            if match:
                match["found_by"] = "parser, last run"
                match["website"] = match["website"] or company.get("website") or ""
            elif name.casefold() in text:              # only what's on the saved copy
                rows.append({"name": name, "website": company.get("website") or "", "found_by": "last run"})
        with out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=GOLD_COLUMNS)
            writer.writeheader()
            for row in sorted(rows, key=lambda r: r["name"].casefold()):
                writer.writerow({"keep": "?", "note": "", **row})
        print(f"{fund_dir.name}: {len(rows)} rows to check")


def _load_records() -> list[dict]:
    from merge_portfolios import clean_portfolio, find_inputs

    records = []
    for path in find_inputs([], False):
        if path.name.startswith(SKIP_OUTPUTS):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        startups, _ = clean_portfolio(path, data)
        records += [{**s, "fund": data.get("fund_name") or path.name} for s in startups]
    return records


def draft_matching(force: bool) -> None:
    from run_matching import profile
    from src.db.resolution import plain, score_pair

    if PAIRS_FILE.exists() and not force:
        print(f"Keeping the existing {PAIRS_FILE.name}")
        return
    records = _load_records()
    profiles = [profile(i, r["name"], r.get("website"), r.get("country"), r["fund"], r.get("description"))
                for i, r in enumerate(records)]
    # Only records sharing a word of 3+ letters can look alike: comparing everything takes hours
    blocks: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        for word in set(re.findall(r"[a-z0-9]{3,}", plain(r["name"]))):
            blocks.setdefault(word, []).append(i)
    seen, by_kind = set(), {kind: [] for kind in SAMPLES}
    for members in blocks.values():
        if len(members) > 40:                      # a word like "bio": too common to mean anything
            continue
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if (i, j) in seen or records[i]["fund"] == records[j]["fund"]:
                    continue
                seen.add((i, j))
                decision, score, features, _ = score_pair(profiles[i], profiles[j])
                kind = ("same website" if features["same_domain"] else "unsure" if decision == "unsure"
                        else "rejected" if decision == "no_match" else "same website")
                by_kind[kind].append((score, i, j, decision))
    rng = random.Random(7)
    chosen = []
    for kind, wanted in SAMPLES.items():
        pool = by_kind[kind]
        if kind == "rejected":                     # the near misses: where a real match could hide
            pool = sorted(pool, key=lambda p: -p[0])[:wanted * 3]
        chosen += [(kind, *p) for p in rng.sample(pool, min(wanted, len(pool)))]
    PAIRS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PAIRS_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PAIR_COLUMNS)
        writer.writeheader()
        for n, (kind, score, i, j, decision) in enumerate(chosen, 1):
            row = {"pair_id": n, "label": "", "suggested": decision, "score": score, "kind": kind, "note": ""}
            for side, r in (("left", records[i]), ("right", records[j])):
                row.update({f"{side}_name": r["name"], f"{side}_website": r.get("website") or "",
                            f"{side}_country": r.get("country") or "", f"{side}_funds": r["fund"],
                            f"{side}_description": r.get("description") or ""})
            writer.writerow(row)
    print(f"{len(chosen)} pairs to check -> {PAIRS_FILE}")
    print({k: len(v) for k, v in by_kind.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Write first drafts of the answer keys.")
    parser.add_argument("task", choices=["detection", "matching"])
    parser.add_argument("--force", action="store_true", help="Overwrite existing files, corrections included")
    args = parser.parse_args()
    (draft_detection if args.task == "detection" else draft_matching)(args.force)


if __name__ == "__main__":
    main()
