"""
pipeline.py — Multi-agent pipeline orchestrator.

Coordinates agent workflows for discovering, extracting, and enriching startups.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from src.custom_agents.portfolio_agent import PortfolioAgent, FinderResult


async def run_portfolio_agent(
    fund_url: str,
    max_turns: int = 25,
) -> FinderResult:
    """
    Invoke the Portfolio Agent asynchronously to extract portfolio startups
    from an investment fund website.
    """
    with PortfolioAgent() as agent:
        return await agent.run(fund_url, max_turns=max_turns)


def run_portfolio_agent_sync(
    fund_url: str,
    max_turns: int = 25,
) -> FinderResult:
    """
    Synchronous wrapper to invoke the Portfolio Agent.
    """
    return asyncio.run(run_portfolio_agent(fund_url, max_turns=max_turns))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <fund_portfolio_url>")
        sys.exit(1)

    target_url = sys.argv[1]
    print(f"Running pipeline on: {target_url}")
    result = run_portfolio_agent_sync(target_url)
    print(f"\nCompleted! Fund: '{result.fund_name}', Found: {len(result.startups)} startup(s).")
    print(result.model_dump_json(indent=2))

