from __future__ import annotations

import asyncio
import logging
from typing import Any

import openai
from agents import (
    Agent,
    AgentsException,
    ItemHelpers,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    Runner,
    ToolsToFinalOutputResult,
    set_tracing_disabled,
)

from src.core import config
from src.custom_agents.base import RunReport
from src.core.llm import OneToolCallPerTurn, create_client, create_model

from .context import worker_run_config
from .instructions import FINDER_AGENT_INSTRUCTIONS
from .models import FinderResult
from .output import write_result
from .heuristics import fund_name_from_title
from .prepass import PrepassResult, run_prepass
from .recorder import PortfolioRecorder, looks_like_broken_tool_call, salvage_companies
from .tools import BrowserTools

set_tracing_disabled(True)

logger = logging.getLogger(__name__)

MODES = ("hybrid", "deterministic", "agent")
MAX_RECOVERIES = 3
RECOVERY_NOTE = (
    "Your last tool call was cut off and could not be read. {saved} companies "
    "from it were saved. {saved_names} Continue with the companies NOT in this "
    "list, using smaller record_companies calls: at most 10 companies each, "
    "`companies` as a JSON array (not a string), one short sentence per "
    "description. Then call finish."
)
LENGTH_NOTE = (
    "Your last reply was too long and was cut off before any tool call could "
    "be read. Reply with exactly ONE tool call. {saved_names} Continue from there."
)
# Needed for gpt-oss-20b
TEXT_REPLY_NOTE = (
    "You replied with text instead of a tool call. Only tool calls are saved. "
    "{saved_names} Save the portfolio companies you found with record_companies "
    "(at most 15 per call), then call finish."
)
MAX_PROMPT_NAMES_CHARS = 3_000


def build_worker_prompt(url: str, prepass: PrepassResult | None) -> str:
    """Task message for the worker, including what the pre-pass already found."""
    task = (
        f"Extract all portfolio companies/startups from the following "
        f"investment fund website: {url}"
    )
    if prepass is None or not prepass.portfolio_url:
        return task
    where = ", ".join(prepass.portfolio_urls or [prepass.portfolio_url])
    if not prepass.companies:
        return (
            f"{task}\n\nThe portfolio is probably at {where}, but "
            f"an automatic parser could not read its companies. Start there."
        )

    names = ", ".join(c.name for c in prepass.companies)
    if len(names) > MAX_PROMPT_NAMES_CHARS:
        names = names[:MAX_PROMPT_NAMES_CHARS].rsplit(", ", 1)[0] + ", ..."
    return f"""{task}

        An automatic parser already found the portfolio at {where}
        and recorded {len(prepass.companies)} companies: {names}

        Your job:
        1. Identify the fund and call record_fund.
        2. Check for companies the parser missed: other tabs, categories,
        pagination or "load more" content. Record only the missing ones.
        3. If a recorded entry is clearly not a portfolio company (a team member,
        partner, LP or news item), remove it with remove_companies.
        4. Do not record the listed companies again. Call finish when done.
    """


class PortfolioAgent:
    """Agent that navigates investment fund websites and extracts portfolio startups.

    The agent saves companies through recorder tools as it browses, so the
    FinderResult is built in Python from what was recorded. A run that hits
    max_turns or a model error still returns the companies found so far.
    """

    def __init__(self) -> None:
        self.browser_tools = BrowserTools()
        self.recorder = PortfolioRecorder(evidence=self.browser_tools.evidence)
        self.client = create_client()
        self.model = OneToolCallPerTurn(create_model(self.client))

        self._worker = Agent(
            name="PortfolioFinderWorker",
            instructions=FINDER_AGENT_INSTRUCTIONS,
            model=self.model,
            model_settings=ModelSettings(
                temperature=config.LLM_TEMPERATURE,
                max_tokens=config.LLM_MAX_TOKENS,
                # "required" makes vLLM force output to the tool schemas: no
                # lists sent as strings, no malformed JSON.
                tool_choice=config.LLM_TOOL_CHOICE or None,
                # With a forced tool_choice, vLLM builds an array of calls with no
                # length limit; Llama 3.1 then emitted 50+ calls per reply. False
                # asks for one call (only the first is executed anyway).
                parallel_tool_calls=False if config.LLM_TOOL_CHOICE else None,
            ),
            # The SDK otherwise resets tool_choice to "auto" after the first tool
            # call. Safe to keep: runs end on an accepted finish() or max_turns.
            reset_tool_choice=not config.LLM_TOOL_CHOICE,
            tools=self.browser_tools.tools + self.recorder.tools,
            # Llama 3.1 with JSON tool calling tends to answer only with tool
            # calls, so the run ends on an accepted finish() call rather than
            # plain text. finish() refuses (and the run continues) until a page
            # has been read.
            tool_use_behavior=self._stop_when_finished,
        )

    def _stop_when_finished(self, context: Any, results: list[Any]) -> ToolsToFinalOutputResult:
        if self.recorder.finished:
            return ToolsToFinalOutputResult(is_final_output=True, final_output="finished")
        return ToolsToFinalOutputResult(is_final_output=False)

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
        self.recorder.site_url = url
        self.browser_tools.evidence.reset()

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

        if not self.recorder.fund_name and not self.recorder.fund_name_hint:
            # The agent never called record_fund: name the fund from the first
            # page it read (og:site_name, else the title) rather than "Unknown"
            evidence = self.browser_tools.evidence
            self.recorder.fund_name_hint = evidence.site_name or fund_name_from_title(evidence.page_title or "")
        return self._finish(url)

    async def _run_worker(self, prompt: str, max_turns: int) -> None:
        """Run the worker, recovering when a tool call comes back unreadable.

        When the model's tool-call JSON is cut off (usually at max_tokens),
        vLLM returns it as plain text and the SDK treats that as the final
        answer. Instead of ending there, salvage complete companies from the
        text, drop it from the history and ask the model to continue.
        """
        logger.info("Worker starting (max_turns=%d)", max_turns)
        run_input: str | list[Any] = prompt
        turns_left = max_turns
        for _ in range(MAX_RECOVERIES + 1):
            try:
                result = await Runner.run(
                    self._worker, run_input,
                    max_turns=turns_left,
                    run_config=worker_run_config(),
                )
            except MaxTurnsExceeded:
                logger.warning("Worker hit max_turns=%d; keeping recorded results", max_turns)
                self.recorder.add_error(
                    f"Stopped after reaching the {max_turns}-turn limit; "
                    f"the portfolio may be incomplete."
                )
                return
            except ModelBehaviorError as exc:
                # A reply that hit max_tokens before any tool call was complete:
                # vLLM returns nothing usable and the SDK raises. Recover like an
                # unreadable tool call instead of failing the whole fund.
                if "finish_reason='length'" not in str(exc) or exc.run_data is None:
                    if not self.recorder.companies:
                        raise
                    logger.error("Worker failed; keeping recorded results", exc_info=True)
                    self.recorder.add_error(f"Run stopped early: {type(exc).__name__}: {exc}")
                    return
                turns_left -= len(exc.run_data.raw_responses) + 1  # +1: the cut-off reply
                logger.warning("Reply cut off at max_tokens with no tool call; %d turns left", turns_left)
                if turns_left <= 0:
                    break
                history = ItemHelpers.input_to_new_input_list(exc.run_data.input) + [
                    item.to_input_item() for item in exc.run_data.new_items
                ]
                note = LENGTH_NOTE.format(saved_names=self.recorder.saved_names_note())
                run_input = history + [{"role": "user", "content": note}]
                continue
            except (AgentsException, openai.APIError) as exc:
                if not self.recorder.companies:
                    raise
                logger.error("Worker failed; keeping recorded results", exc_info=True)
                self.recorder.add_error(f"Run stopped early: {type(exc).__name__}: {exc}")
                return

            text = str(result.final_output or "")
            logger.debug("Worker final message:\n%s", text)
            if self.recorder.finished:
                return
            if not looks_like_broken_tool_call(text):
                # A plain-text reply ends an SDK run, but nothing says the work
                # is done: models with a separate "final answer" channel
                # (gpt-oss) often answer in prose instead of calling a tool.
                # Keep the reply (it may list companies) and ask for tool calls.
                turns_left -= len(result.raw_responses)
                logger.warning("Worker replied with text instead of a tool call; %d turns left", turns_left)
                if turns_left <= 0:
                    break
                note = TEXT_REPLY_NOTE.format(saved_names=self.recorder.saved_names_note())
                run_input = result.to_input_list() + [{"role": "user", "content": note}]
                continue
            # Check evidence
            salvaged, _ = self.recorder.check_evidence(salvage_companies(text))
            saved, _ = self.recorder.add_companies(salvaged)
            turns_left -= len(result.raw_responses)
            logger.warning(
                "Unreadable tool call (%d chars); salvaged %d companies, %d turns left",
                len(text), saved, turns_left,
            )
            if turns_left <= 0:
                break
            history = result.to_input_list()
            while history and isinstance(history[-1], dict) and history[-1].get("role") == "assistant":
                history.pop()  # the unreadable reply only wastes context
            note = RECOVERY_NOTE.format(saved=saved, saved_names=self.recorder.saved_names_note())
            run_input = history + [{"role": "user", "content": note}]

        self.recorder.add_error(
            "The model repeatedly replied without a usable tool call (cut off, "
            "unreadable or plain text); the portfolio may be incomplete."
        )

    def _finish(self, url: str) -> FinderResult:
        output = self.recorder.build_result(url)
        logger.info(
            "Run complete: fund='%s', startups=%d, errors=%d",
            output.fund_name, len(output.startups), len(output.errors),
        )
        try:
            path = write_result(output, url)
            logger.info("Output written to %s", path)
        except Exception:
            logger.error("Failed to write output file", exc_info=True)

        return output

    def run_sync(
        self, url: str, max_turns: int = 25, mode: str | None = None,
    ) -> FinderResult:
        """Synchronous convenience wrapper to run portfolio extraction."""
        return asyncio.run(self.run(url, max_turns=max_turns, mode=mode))


async def run_portfolio(
    url: str, *, max_turns: int = 25, mode: str | None = None, **_: Any,
) -> RunReport:
    """Pipeline entry point: extract one fund's portfolio (see AgentSpec)."""
    with PortfolioAgent() as agent:
        try:
            result = await agent.run(url, max_turns=max_turns, mode=mode)
        finally:
            await agent.client.close()  # inside this run's event loop
    return RunReport(
        summary=f"Fund '{result.fund_name}', {len(result.startups)} startup(s)",
        items=len(result.startups),
        errors=list(result.errors),
    )
