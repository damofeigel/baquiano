"""The contract every extraction agent in custom_agents/ fulfils.

To add an agent:
  1. Create a package in src/custom_agents/ (e.g. news_agent/).
  2. Write an async run(url, **options) -> RunReport that does the extraction,
     writes its own output, and ignores options it doesn't use.
  3. Expose SPEC = AgentSpec(name=..., description=..., run=...) from the package.
  4. Add it to AGENTS in src/custom_agents/registry.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


@dataclass
class RunReport:
    """What the pipeline needs to report on one URL, whatever the agent."""

    summary: str              # one line for the console, e.g. "Fund 'GRIDX', 86 startup(s)"
    items: int                # records extracted; 0 is reported as an empty result
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentSpec:
    name: str                 # what URLs use as their prefix, e.g. "portfolio"
    description: str          # shown by pipeline.py --list-agents
    run: Callable[..., Coroutine[Any, Any, RunReport]]  # async run(url, **options) -> RunReport
