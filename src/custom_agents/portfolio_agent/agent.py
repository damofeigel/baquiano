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
from .prepass import PrepassResult, run_prepass
from .recorder import PortfolioRecorder
from .tools import BrowserTools

set_tracing_disabled(True)

logger = logging.getLogger(__name__)

MODES = ("hybrid", "deterministic", "agent")
MAX_PROMPT_NAMES_CHARS = 3_000


def build_worker_prompt(url: str, prepass: PrepassResult | None) -> str:
    """Task message for the worker, including what the pre-pass already found."""
    task = (
        f"Extract all portfolio companies/startups from the following "
        f"investment fund website: {url}"
    )
    if prepass is None or not prepass.portfolio_url:
        return task
    if not prepass.companies:
        return (
            f"{task}\n\nThe portfolio is probably at {prepass.portfolio_url}, but "
            f"an automatic parser could not read its companies. Start there."
        )

    names = ", ".join(c.name for c in prepass.companies)
    if len(names) > MAX_PROMPT_NAMES_CHARS:
        names = names[:MAX_PROMPT_NAMES_CHARS].rsplit(", ", 1)[0] + ", ..."
    return f"""{task}

An automatic parser already found the portfolio at {prepass.portfolio_url}
and recorded {len(prepass.companies)} companies: {names}

Your job:
1. Identify the fund and call record_fund.
2. Check for companies the parser missed: other tabs, categories,
   pagination or "load more" content. Record only the missing ones.
3. If a recorded entry is clearly not a portfolio company (a team member,
   partner, LP or news item), remove it with remove_companies.
4. Do not record the listed companies again. Call finish when done."""


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
            # Llama 3.1 with JSON tool calling tends to answer only with tool
            # calls, so the run ends on a finish() call rather than plain text.
            tool_use_behavior={"stop_at_tool_names": ["finish"]},
        )

    def close(self) -> None:
        """Close browser resources and background thread pools."""
        self.browser_tools.close()

    def __enter__(self) -> PortfolioAgent:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    async def run(
        self, url: str, max_turns: int = 25, mode: str | None = None,
    ) -> FinderResult:
        """Extract the portfolio of a fund URL and return the recorded companies.

        mode: "hybrid" (pre-pass, then agent), "deterministic" (pre-pass only)
        or "agent" (agent only). Defaults to config.PORTFOLIO_MODE.
        """
        mode = (mode or config.PORTFOLIO_MODE).lower()
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}")
        self.recorder.reset()

        prepass: PrepassResult | None = None
        if mode in ("hybrid", "deterministic"):
            prepass = await run_prepass(self.browser_tools, url)
            self.recorder.fund_name_hint = prepass.fund_name_hint
            self.recorder.source_url = prepass.portfolio_url
            self.recorder.add_companies(prepass.companies)
            logger.info(
                "Pre-pass: portfolio=%s, companies=%d, notes=%s",
                prepass.portfolio_url, len(self.recorder.companies), prepass.notes,
            )

        if mode == "deterministic":
            if prepass and not self.recorder.companies:
                for note in prepass.notes:
                    self.recorder.add_error(note)
        else:
            await self._run_worker(build_worker_prompt(url, prepass), max_turns)

        return self._finish(url)

    async def _run_worker(self, prompt: str, max_turns: int) -> None:
        logger.info("Worker starting (max_turns=%d)", max_turns)
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

    def _finish(self, url: str) -> FinderResult:
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

    def run_sync(
        self, url: str, max_turns: int = 25, mode: str | None = None,
    ) -> FinderResult:
        """Synchronous convenience wrapper to run portfolio extraction."""
        return asyncio.run(self.run(url, max_turns=max_turns, mode=mode))
