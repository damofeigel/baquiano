from __future__ import annotations

import asyncio
import logging
from typing import Any

import openai
from agents import (
    Agent,
    AgentsException,
    MaxTurnsExceeded,
    ModelSettings,
    Runner,
    set_tracing_disabled,
)

from src.core import config
from src.core.llm import get_client, get_model

from .context import worker_run_config
from .instructions import FINDER_AGENT_INSTRUCTIONS
from .models import FinderResult
from .recorder import PortfolioRecorder
from .tools import BrowserTools

set_tracing_disabled(True)

logger = logging.getLogger(__name__)


class PortfolioAgent:
    """Agent that navigates investment fund websites and extracts portfolio startups.

    The agent saves companies through recorder tools as it browses, so the
    FinderResult is built in Python from what was recorded. A run that hits
    max_turns or a model error still returns the companies found so far.
    """

    def __init__(self) -> None:
        self.browser_tools = BrowserTools()
        self.recorder = PortfolioRecorder()
        self.client = get_client()
        self.model = get_model()

        self._worker = Agent(
            name="PortfolioFinderWorker",
            instructions=FINDER_AGENT_INSTRUCTIONS,
            model=self.model,
            model_settings=ModelSettings(
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
            ),
            tools=self.browser_tools.tools + self.recorder.tools,
        )

    def close(self) -> None:
        """Close browser resources and background thread pools."""
        self.browser_tools.close()

    def __enter__(self) -> PortfolioAgent:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    async def run(self, url: str, max_turns: int = 25) -> FinderResult:
        """Browse a fund portfolio URL and return the recorded companies."""
        self.recorder.reset()
        prompt = (
            f"Extract all portfolio companies/startups from the following "
            f"investment fund website: {url}"
        )

        logger.info("Worker browsing %s (max_turns=%d)", url, max_turns)
        try:
            worker_result = await Runner.run(
                self._worker, prompt,
                max_turns=max_turns,
                run_config=worker_run_config(),
            )
            logger.debug("Worker final message:\n%s", worker_result.final_output)
        except MaxTurnsExceeded:
            logger.warning("Worker hit max_turns=%d; keeping recorded results", max_turns)
            self.recorder.add_error(
                f"Stopped after reaching the {max_turns}-turn limit; "
                f"the portfolio may be incomplete."
            )
        except (AgentsException, openai.APIError) as exc:
            if not self.recorder.companies:
                raise
            logger.error("Worker failed; keeping recorded results", exc_info=True)
            self.recorder.add_error(f"Run stopped early: {type(exc).__name__}: {exc}")

        output = self.recorder.build_result(url)
        logger.info(
            "Run complete: fund='%s', startups=%d, errors=%d",
            output.fund_name, len(output.startups), len(output.errors),
        )
        try:
            config.OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(config.OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(output.model_dump_json(indent=2))
            logger.info("Output written to %s", config.OUTPUT_FILE)
        except Exception:
            logger.error("Failed to write output file", exc_info=True)

        return output

    def run_sync(self, url: str, max_turns: int = 25) -> FinderResult:
        """Synchronous convenience wrapper to run portfolio extraction."""
        return asyncio.run(self.run(url, max_turns=max_turns))
