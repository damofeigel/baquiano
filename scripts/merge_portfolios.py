#!/usr/bin/env python3
"""
merge_portfolios.py — Merge per-fund portfolio outputs into one startup list.

Usage:
    python scripts/merge_portfolios.py                      # latest output of each fund in output/
    python scripts/merge_portfolios.py output/a.json output/b.json
    python scripts/merge_portfolios.py --all                # every portfolio file, not just the latest

The same startup often appears in several funds (Beeflow: GRIDX and The Yield
Lab). Startups are matched by website domain, then by name (case, accents and
punctuation ignored; "Heartbest" = "Heartbest Foods"), the same rules the
portfolio agent uses within a run. Each merged startup lists every fund and
source page it came from; fields that disagree between funds are kept under
"conflicts" instead of being silently overwritten.

Cleaning on the way in: a fund's regional sister site counts as the same fund
(theyieldlablatam.com is The Yield Lab); a sector tag or badge recorded as a
name ("AI & SaaS", "Exited") is renamed from the startup's website, or dropped
when it has none; countries get one spelling ("USA" -> "United States").

Writes output/startups_merged_<time>.json and a .csv next to it.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.matching import (  # noqa: E402
    dedupe_key, looks_like_category, name_from_website, normalize_country, registrable_domain, same_company_name,
    sister_of, website_domain,
)

OUTPUT_DIR = PROJECT_ROOT / "output"
STAMP = re.compile(r"_(\d{8}-\d{6})(?:-\d+)?\.json$")
FIELDS = ["website", "country", "year_created", "sector", "email", "phone", "funding_round", "funding_amount_usd"]


def is_portfolio(data: object) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("startups"), list)
        and "fund_name" in data and "source_url" in data
    )


def fund_site(path: Path) -> tuple[str, str]:
    """(fund site, timestamp) from the <site>_<YYYYmmdd-HHMMSS>.json naming."""
    match = STAMP.search(path.name)
    return (path.name[: match.start()], match.group(1)) if match else (path.stem, "")


def latest_per_fund(files: list[Path]) -> list[Path]:
    """The newest file per fund site."""
    newest: dict[str, tuple[str, Path]] = {}
    for f in files:
        site, stamp = fund_site(f)
        if site not in newest or stamp > newest[site][0]:
            newest[site] = (stamp, f)
    return sorted(path for _, path in newest.values())


def fund_names(inputs: list[Path]) -> dict[Path, str]:
    """One display name per fund site, from its newest file.

    Runs of the same fund can name it differently ("Grid Exponential" from the
    agent, "GRIDX" from the pre-pass); without this, --all lists it twice. A
    regional sister site takes its main site's name ("The Yield Lab Latam" ->
    "The Yield Lab"), since the main site's run already follows its sisters.
    """
    newest: dict[str, tuple[str, str]] = {}
    for path in inputs:
        site, stamp = fund_site(path)
        name = json.loads(path.read_text(encoding="utf-8")).get("fund_name") or "Unknown"
        if name != "Unknown" and (site not in newest or stamp > newest[site][0]):
            newest[site] = (stamp, name)
    sites = sorted({fund_site(path)[0] for path in inputs})

    def name_of(site: str) -> str:
        parent = sister_of(site, sites)
        if parent and parent in newest:
            return newest[parent][1]
        return newest.get(site, ("", "Unknown"))[1]

    return {path: name_of(fund_site(path)[0]) for path in inputs}


def clean_startup(startup: dict, fund_domains: set[str] | None = None) -> tuple[dict | None, str | None]:
    """(the record to merge or None to drop it, a note on what changed).

    A tag or badge read as the name ("AI & SaaS", "Exited") is replaced by a
    name from the startup's website; without one there is nothing to identify.
    A link to the fund's own site (FUND_DOMAINS) doesn't count as the startup's.
    """
    startup = {**startup, "country": normalize_country(startup.get("country"))}
    name = startup["name"]
    if not looks_like_category(name, startup.get("sector")):
        return startup, None
    better = name_from_website(startup.get("website"), fund_domains)
    if not better:
        why = ("its only link is the fund's own site or a shared platform" if startup.get("website")
               else "no website")
        return None, f"dropped '{name}': a category, not a company name, and {why}"
    description = startup.get("description")
    if description and description.strip().casefold() == name.strip().casefold():
        description = None                       # the tag repeated as a description
    return ({**startup, "name": better, "description": description, "renamed_from": name},
            f"renamed '{name}' to '{better}' (from {startup.get('website')})")


def find_inputs(paths: list[Path], include_all: bool) -> list[Path]:
    if paths:
        return paths
    candidates = []
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        if f.name.startswith(("lens_", "startups_")):
            continue
        try:
            if is_portfolio(json.loads(f.read_text(encoding="utf-8"))):
                candidates.append(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return candidates if include_all else latest_per_fund(candidates)


def find_match(merged: list[dict], startup: dict) -> dict | None:
    domain = website_domain(startup.get("website"))
    if domain:
        for entry in merged:
            if domain in entry["_domains"]:
                return entry
    for entry in merged:
        if any(same_company_name(startup["name"], alias) or dedupe_key(startup["name"]) == dedupe_key(alias)
               for alias in entry["aliases"]):
            return entry
    return None


def merge(inputs: list[Path], notes: list[str] | None = None) -> list[dict]:
    """Merged startups; what cleaning changed is appended to NOTES."""
    notes = [] if notes is None else notes
    merged: list[dict] = []
    names = fund_names(inputs)
    for path in inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        fund = names[path]
        fund_domains = {d for d in (registrable_domain(fund_site(path)[0]),
                                    registrable_domain(website_domain(data.get("source_url")))) if d}
        for raw in data["startups"]:
            if not raw.get("name"):
                continue
            startup, note = clean_startup(raw, fund_domains)
            if note:
                notes.append(f"{fund}: {note}")
            if startup is None:
                continue
            entry = find_match(merged, startup)
            if entry is None:
                entry = {
                    "name": startup["name"], "aliases": [], "description": None, "descriptions": [],
                    "funds": [], "sources": [], "conflicts": {}, "_domains": set(),
                    "_name_from_website": bool(startup.get("renamed_from")),
                    **{field: None for field in FIELDS},
                }
                merged.append(entry)
            if startup.get("renamed_from"):
                if startup["renamed_from"] not in entry.setdefault("renamed_from", []):
                    entry["renamed_from"].append(startup["renamed_from"])
            else:
                if entry["_name_from_website"]:
                    # A real name from another fund beats one made from the domain
                    entry["name"], entry["_name_from_website"] = startup["name"], False
                if startup["name"] not in entry["aliases"]:
                    entry["aliases"].append(startup["name"])
            if fund not in entry["funds"]:
                entry["funds"].append(fund)
            source = startup.get("source_url") or data.get("source_url")
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
            if startup.get("description"):
                entry["descriptions"].append({"fund": fund, "text": startup["description"]})
            for field in FIELDS:
                value = startup.get(field)
                if value in (None, ""):
                    continue
                if entry[field] is None:
                    entry[field] = value
                elif _differs(field, entry[field], value):
                    entry["conflicts"].setdefault(field, [entry[field]])
                    if value not in entry["conflicts"][field]:
                        entry["conflicts"][field].append(value)
            if domain := website_domain(startup.get("website")):
                entry["_domains"].add(domain)

    for entry in merged:
        # The longest description is usually the most informative
        if entry["descriptions"]:
            entry["description"] = max((d["text"] for d in entry["descriptions"]), key=len)
        del entry["_domains"], entry["_name_from_website"]
    return sorted(merged, key=lambda e: e["name"].casefold())


def _differs(field: str, a: object, b: object) -> bool:
    if field == "website":
        return website_domain(str(a)) != website_domain(str(b))
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() != b.strip().casefold()
    return a != b


def write_csv(path: Path, startups: list[dict]) -> None:
    columns = ["name", "country", "website", "funds", "description", "sector", "year_created", "aliases", "conflicts"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for s in startups:
            writer.writerow({
                **{c: s.get(c) for c in columns},
                "funds": "; ".join(s["funds"]),
                "aliases": "; ".join(a for a in s["aliases"] if a != s["name"]),
                "conflicts": json.dumps(s["conflicts"], ensure_ascii=False) if s["conflicts"] else "",
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge per-fund portfolio outputs into one startup list.")
    parser.add_argument("files", nargs="*", type=Path, help="Portfolio output files (default: latest per fund in output/)")
    parser.add_argument("--all", action="store_true", help="Use every portfolio file, not only the latest per fund")
    parser.add_argument("--out", type=Path, help="Output JSON (default: output/startups_merged_<time>.json)")
    args = parser.parse_args()

    inputs = find_inputs(args.files, args.all)
    if not inputs:
        sys.exit("No portfolio output files found.")
    notes: list[str] = []
    startups = merge(inputs, notes)

    out = args.out or OUTPUT_DIR / f"startups_merged_{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    countries = Counter(s["country"] or "unknown" for s in startups)
    multi = [s for s in startups if len(s["funds"]) > 1]
    conflicted = [s for s in startups if s["conflicts"]]
    result = {
        "merged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": [str(p) for p in inputs],
        "summary": {
            "startups": len(startups),
            "in_several_funds": len(multi),
            "argentina": countries.get("Argentina", 0),
            "country_unknown": countries.get("unknown", 0),
            "with_conflicts": len(conflicted),
            "cleaned": len(notes),
            "by_country": dict(countries.most_common()),
        },
        "cleaning": notes,
        "startups": startups,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out.with_suffix(".csv"), startups)

    print(f"Merged {sum(1 for _ in inputs)} file(s) into {len(startups)} startups -> {out}")
    for p in inputs:
        print(f"  {p.name}")
    print(f"  in several funds: {len(multi)}  | Argentina: {countries.get('Argentina', 0)}"
          f"  | country unknown: {countries.get('unknown', 0)}  | with conflicts: {len(conflicted)}")
    for s in multi[:10]:
        print(f"    {s['name']}: {', '.join(s['funds'])}")
    if notes:
        print(f"  cleaned {len(notes)} record(s):")
        for note in notes:
            print(f"    {note}")


if __name__ == "__main__":
    main()
