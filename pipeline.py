"""
pipeline.py — Multi-agent pipeline orchestrator.

Runs the agent each URL names: "AGENT:URL", e.g. portfolio:https://www.gridexponential.com.
Available agents are registered in src/custom_agents/registry.py.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from src.core import config
from src.custom_agents.base import RunReport
from src.custom_agents.portfolio_agent.agent import MODES
from src.custom_agents.registry import AGENTS

TARGETS_FILE = config.PROJECT_ROOT / "targets.txt"


@dataclass
class Target:
    """A URL and the name of the agent that extracts it."""
    agent: str
    url: str


def parse_target(spec: str) -> Target:
    """Parse "AGENT:URL", e.g. "portfolio:https://fund.example".

    The agent is required and must be registered. A URL without a scheme gets
    https://. Raises ValueError with a message naming the available agents.
    """
    spec = spec.strip()
    available = ", ".join(sorted(AGENTS))
    head, sep, rest = spec.partition(":")
    if not sep or head.lower() in ("http", "https"):
        raise ValueError(
            f"missing agent in {spec!r}; write AGENT:URL, e.g. portfolio:{spec} "
            f"(available: {available})"
        )
    agent, url = head.strip().lower(), rest.strip()
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {head!r} in {spec!r} (available: {available})")
    if not url:
        raise ValueError(f"missing URL in {spec!r}")
    if "://" not in url:
        url = f"https://{url}"
    return Target(agent=agent, url=url)


def load_targets(cli_specs: list[str], file: Path = TARGETS_FILE) -> list[Target]:
    """Targets from the command line (space or comma separated), else from file.

    The file has one "AGENT:URL" per line; blank lines and # comments are ignored.
    Every entry is validated before any run starts. Raises ValueError listing all
    invalid entries.
    """
    if cli_specs:
        entries = [("argument", part) for arg in cli_specs for part in arg.split(",")]
    else:
        lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
        # "#" starts a comment only at line start or after whitespace: URLs may
        # contain "#" (e.g. https://fund.example/#/portfolio)
        entries = [
            (f"{file.name}:{n}", re.sub(r"(^|\s)#.*$", "", line))
            for n, line in enumerate(lines, 1)
        ]

    targets, problems = [], []
    for where, spec in entries:
        if not spec.strip():
            continue
        try:
            targets.append(parse_target(spec))
        except ValueError as exc:
            problems.append(f"{where}: {exc}")
    if problems:
        raise ValueError("invalid entries:\n  " + "\n  ".join(problems))
    return targets


def run_target(target: Target, **options) -> RunReport:
    """Run the target's agent synchronously. Agents ignore options they don't use."""
    return asyncio.run(AGENTS[target.agent].run(target.url, **options))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract startup data with the agent each URL names.",
        epilog="Example: python pipeline.py portfolio:https://www.gridexponential.com",
    )
    parser.add_argument(
        "targets", nargs="*", metavar="AGENT:URL",
        help="URLs prefixed with the agent to use (space or comma separated). "
             "If omitted, read from targets.txt, one per line.",
    )
    parser.add_argument("--list-agents", action="store_true", help="List available agents and exit")
    parser.add_argument(
        "--mode", choices=MODES,
        help="Portfolio agent only: extraction mode "
             "(default: PORTFOLIO_MODE from .env, else hybrid)",
    )
    parser.add_argument("--max-turns", type=int, default=25, help="Turn limit for LLM agents")
    args = parser.parse_args()

    if args.list_agents:
        for name, spec in sorted(AGENTS.items()):
            print(f"{name:12} {spec.description}")
        raise SystemExit(0)

    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        targets = load_targets(args.targets)
    except ValueError as exc:
        parser.error(str(exc))
    if not targets:
        parser.error("no URLs given and targets.txt is empty or missing")

    failed: list[str] = []
    empty: list[str] = []
    for i, target in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {target.agent}: {target.url}")
        try:
            report = run_target(target, max_turns=args.max_turns, mode=args.mode)
        except Exception as exc:
            logging.exception("Failed on %s", target.url)
            failed.append(f"{target.agent}:{target.url} ({type(exc).__name__}: {exc})")
            continue
        print(f"  {report.summary}, {len(report.errors)} error(s)")
        if not report.items:
            # Often not a crash: unreachable site, private portfolio, unparseable page
            empty.append(
                f"{target.agent}:{target.url} "
                f"({'; '.join(report.errors) or 'no errors reported'})"
            )

    ok = len(targets) - len(failed) - len(empty)
    print(f"\nDone: {ok} with results, {len(empty)} empty, {len(failed)} failed "
          f"(of {len(targets)}).")
    for line in empty:
        print(f"  EMPTY:  {line}")
    for line in failed:
        print(f"  FAILED: {line}")
