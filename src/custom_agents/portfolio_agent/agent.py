from __future__ import annotations

import asyncio
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    Runner,
    set_tracing_disabled,
)

from src.core import config
from src.core.llm import get_client, get_model

from .instructions import FINDER_AGENT_INSTRUCTIONS
from .models import FinderResult
from .tools import BrowserTools

set_tracing_disabled(True)


class PortfolioAgent:
    """Agent that navigates investment fund websites and extracts portfolio startups."""

    def __init__(self) -> None:
        self.browser_tools = BrowserTools()
        self.client = get_client()
        self.model = get_model()

        self.agent = Agent(
            name="PortfolioFinderAgent",
            instructions=FINDER_AGENT_INSTRUCTIONS,
            output_type=FinderResult,
            model=self.model,
            model_settings=ModelSettings(
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
            ),
            tools=self.browser_tools.tools,
        )

    def close(self) -> None:
        """Close browser resources and background thread pools."""
        self.browser_tools.close()

    def __enter__(self) -> PortfolioAgent:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    async def run(self, url: str, max_turns: int = 15) -> FinderResult:
        """Execute the agent extraction loop asynchronously for a given fund portfolio URL."""
        prompt = f"Extract all portfolio companies/startups from the following investment fund website: {url}"
        result = await Runner.run(self.agent, prompt, max_turns=max_turns)
        output = result.final_output
        if isinstance(output, FinderResult):
            try:
                config.OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(config.OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(output.model_dump_json(indent=2))
            except Exception:
                pass
        return output

    def run_sync(self, url: str, max_turns: int = 15) -> FinderResult:
        """Synchronous convenience wrapper to run portfolio extraction."""
        return asyncio.run(self.run(url, max_turns=max_turns))
