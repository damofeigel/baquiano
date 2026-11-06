#!/usr/bin/env python3
"""
run_detection.py — Run each setting on each saved fund page and score what it finds.

Usage:
    python evaluation/run_detection.py                               # every setting and fund
    python evaluation/run_detection.py --setting "parser only"       # no model server needed
    python evaluation/run_detection.py --fund kaszek sosv --repeats 3

The real PortfolioAgent runs, but the browser only sees the saved copies: any other
address answers "not saved". Names go through the same cleaning as the merge step.
A finished test is skipped, so a stopped run continues where it was (--fresh starts over).
Writes evaluation/results/detection.jsonl; nothing goes to output/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from common import (
    DETECTION_DIR, RESULTS_DIR, Usage, append_result, applied, load_gold, load_settings, read_results, score_detection,
    uses_model, wrap_async_client,
)
from snapshot_pages import page_key
from src.core import config

RESULTS_FILE = RESULTS_DIR / "detection.jsonl"
NOT_SAVED = "<html><head><title>Not saved</title></head><body><h1>This page is not available</h1></body></html>"


def replay_saved_pages(browser: Any, fund_dir: Path, pages: dict[str, str]) -> None:
    """Serve page requests from the saved copies; block everything else (images, styles, other sites)."""
    def handle(route: Any) -> None:
        request = route.request
        if request.resource_type != "document":
            route.abort()
            return
        name = pages.get(page_key(request.url))
        body = (fund_dir / "pages" / name).read_text(encoding="utf-8") if name else NOT_SAVED
        route.fulfill(status=200 if name else 404, content_type="text/html; charset=utf-8", body=body)

    browser.page.context.route("**/*", handle)


async def run_agent(setting: dict[str, Any], fund_dir: Path, meta: dict[str, Any], usage: Usage) -> list[dict[str, Any]]:
    from dataclasses import replace

    from src.custom_agents.portfolio_agent.agent import PortfolioAgent

    with PortfolioAgent() as agent:
        wrap_async_client(agent.client, usage)
        if "extra_body" in setting:
            agent._worker.model_settings = replace(agent._worker.model_settings, extra_body=setting["extra_body"])
        await agent.browser_tools.run_with_browser(lambda b: replay_saved_pages(b, fund_dir, meta["pages"]))
        try:
            result = await agent.run(meta["url"], max_turns=setting.get("max_turns", 25), mode=setting["mode"])
        finally:
            await agent.client.close()
    return [company.model_dump() for company in result.startups], result.errors


def clean_found(found: list[dict[str, Any]], fund_url: str, fund_name: str | None) -> list[dict[str, Any]]:
    from merge_portfolios import clean_startup, fund_domains

    domains = fund_domains(Path("x.json"), {"source_url": fund_url})
    cleaned = []
    for company in found:
        startup, _ = clean_startup(company, domains, fund_name)
        if startup is not None:
            cleaned.append(startup)
    return cleaned


def run_one(setting: dict[str, Any], fund_dir: Path, repeat: int) -> dict[str, Any]:
    meta = json.loads((fund_dir / "meta.json").read_text(encoding="utf-8"))
    gold = load_gold(fund_dir)
    usage = Usage()
    started = time.perf_counter()
    row: dict[str, Any] = {"setting": setting["name"], "fund": fund_dir.name, "repeat": repeat,
                           "model": config.VLLM_MODEL if uses_model(setting) else None,
                           "reviewed": gold.reviewed, "params": {k: v for k, v in setting.items() if k != "api_key"}}
    try:
        found, errors = asyncio.run(run_agent(setting, fund_dir, meta, usage))
        cleaned = clean_found(found, meta["url"], None)
        row["found"] = [{"name": c["name"], "website": c.get("website")} for c in cleaned]
        row.update(score_detection(row["found"], gold))
        row["errors"] = errors
    except Exception as exc:                       # one failure doesn't lose the rest of a long run
        row["failed"] = f"{type(exc).__name__}: {exc}"[:300]
    row.update(seconds=round(time.perf_counter() - started, 1), usage=usage.as_dict())
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Score portfolio detection on the saved fund pages.")
    parser.add_argument("--setting", nargs="+", help="Only these settings (names from settings.json)")
    parser.add_argument("--fund", nargs="+", help="Only these funds")
    parser.add_argument("--repeats", type=int, default=1, help="Runs per fund, for steadier times")
    parser.add_argument("--fresh", action="store_true", help="Forget earlier results and start over")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    if args.fresh:
        RESULTS_FILE.unlink(missing_ok=True)
    done = {(r["setting"], r["fund"], r["repeat"]) for r in read_results(RESULTS_FILE) if "failed" not in r}
    funds = [p for p in sorted(DETECTION_DIR.iterdir()) if (p / "gold.csv").exists()
             and (not args.fund or p.name in args.fund)]
    if not funds:
        sys.exit("No funds with an answer key. Run snapshot_pages.py and draft_labels.py detection first.")

    with tempfile.TemporaryDirectory() as tmp:
        config.OUTPUT_DIR = Path(tmp)              # the agent's own output files stay out of output/
        config.WAIT_AFTER_LOAD = 0                 # saved pages have nothing left to load
        for setting in load_settings("detection", args.setting):
            with applied(setting):
                if uses_model(setting):
                    import openai

                    from src.core.structured_llm import check_llm

                    problem = check_llm(openai.OpenAI(base_url=config.VLLM_BASE_URL, api_key=config.API_KEY),
                                        config.VLLM_MODEL)
                    if problem:
                        print(f"SKIPPING '{setting['name']}': {problem}")
                        continue
                for fund_dir in funds:
                    for repeat in range(args.repeats):
                        if (setting["name"], fund_dir.name, repeat) in done:
                            continue
                        row = run_one(setting, fund_dir, repeat)
                        append_result(RESULTS_FILE, row)
                        outcome = row.get("failed") or (f"{row['correct']} right, {row['wrong']} wrong, "
                                                         f"{row['missed']} missed")
                        print(f"{setting['name']} | {fund_dir.name}: {outcome} ({row['seconds']}s)", flush=True)
    print(f"Results: {RESULTS_FILE}. Graphs: python evaluation/plot_results.py")


if __name__ == "__main__":
    main()
