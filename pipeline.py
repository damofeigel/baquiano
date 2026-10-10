"""
pipeline.py — Multi-agent pipeline orchestrator.

Coordinates agent workflows for discovering, extracting, and enriching startups.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from src.core import config
from src.custom_agents.portfolio_agent import PortfolioAgent, FinderResult


async def run_portfolio_agent(
    fund_url: str,
    max_turns: int = 25,
    mode: Optional[str] = None,
) -> FinderResult:
    """
    Invoke the Portfolio Agent asynchronously to extract portfolio startups
    from an investment fund website.
    """
    with PortfolioAgent() as agent:
        return await agent.run(fund_url, max_turns=max_turns, mode=mode)


def run_portfolio_agent_sync(
    fund_url: str,
    max_turns: int = 25,
    mode: Optional[str] = None,
) -> FinderResult:
    """
    Synchronous wrapper to invoke the Portfolio Agent.
    """
    return asyncio.run(run_portfolio_agent(fund_url, max_turns=max_turns, mode=mode))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract a fund's portfolio companies.")
    parser.add_argument("url", help="Fund website or portfolio page URL")
    parser.add_argument(
        "--mode", choices=["hybrid", "deterministic", "agent"],
        help="Extraction mode (default: PORTFOLIO_MODE from .env, else hybrid)",
    )
    parser.add_argument("--max-turns", type=int, default=25)
    args = parser.parse_args()

    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print(f"Running pipeline on: {args.url}")
    result = run_portfolio_agent_sync(args.url, max_turns=args.max_turns, mode=args.mode)
    print(f"\nCompleted! Fund: '{result.fund_name}', Found: {len(result.startups)} startup(s).")
    print(result.model_dump_json(indent=2))
