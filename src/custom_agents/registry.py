"""Agents the pipeline can run, keyed by the name URLs use as their prefix."""

from __future__ import annotations

from .base import AgentSpec
from .portfolio_agent import SPEC as PORTFOLIO

AGENTS: dict[str, AgentSpec] = {spec.name: spec for spec in (PORTFOLIO,)}
