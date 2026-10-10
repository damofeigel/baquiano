"""Record-keeping tools for the portfolio agent.

Companies are stored in Python as the agent finds them, so a run that hits
max_turns or a model error still returns everything recorded up to that point.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from agents import function_tool

from .models import FinderResult, StartupInfo

logger = logging.getLogger(__name__)


def _dedupe_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.") or None


class PortfolioRecorder:
    """Accumulates fund identity, companies and errors across one agent run."""

    def __init__(self) -> None:
        self.tools = self._build_tools()
        self.reset()

    def reset(self) -> None:
        self.fund_name: str | None = None
        self.source_url: str | None = None
        self.errors: list[str] = []
        self._companies: dict[str, StartupInfo] = {}

    @property
    def companies(self) -> list[StartupInfo]:
        return list(self._companies.values())

    def set_fund(self, fund_name: str, source_url: str | None = None) -> None:
        self.fund_name = fund_name.strip() or self.fund_name
        if source_url:
            self.source_url = source_url.strip()

    def add_companies(self, companies: list[StartupInfo]) -> tuple[int, int]:
        """Add or merge companies. Returns (new, merged) counts."""
        added = merged = 0
        for company in companies:
            if not company.name or not company.name.strip():
                continue
            key = self._find_existing(company)
            if key is None:
                self._companies[_dedupe_key(company.name)] = company
                added += 1
            else:
                existing = self._companies[key]
                # Fill only fields that are still empty
                updates = {
                    field: value
                    for field, value in company.model_dump().items()
                    if value is not None and getattr(existing, field) is None
                }
                self._companies[key] = existing.model_copy(update=updates)
                merged += 1
        return added, merged

    def _find_existing(self, company: StartupInfo) -> str | None:
        key = _dedupe_key(company.name)
        if key in self._companies:
            return key
        domain = _domain(company.website)
        if domain:
            for existing_key, existing in self._companies.items():
                if _domain(existing.website) == domain:
                    return existing_key
        return None

    def add_error(self, message: str) -> None:
        message = message.strip()
        if message and message not in self.errors:
            self.errors.append(message)

    def build_result(self, fallback_url: str) -> FinderResult:
        errors = list(self.errors)
        if not self.fund_name:
            errors.append("Fund name was not identified.")
        return FinderResult(
            source_url=self.source_url or fallback_url,
            fund_name=self.fund_name or "Unknown",
            startups=[c.model_copy() for c in self.companies],
            errors=errors,
        )

    def _build_tools(self) -> list[Any]:
        @function_tool(strict_mode=False)
        def record_fund(fund_name: str, portfolio_url: str = "") -> str:
            """Save the fund's name and the URL of its portfolio page."""
            self.set_fund(fund_name, portfolio_url or None)
            logger.info("Recorded fund: %s (%s)", self.fund_name, self.source_url)
            return json.dumps({"ok": True, "fund_name": self.fund_name})

        @function_tool(strict_mode=False)
        def record_companies(companies: list[StartupInfo]) -> str:
            """Save portfolio companies found on the page you just read.

            Call this right after reading each portfolio page, before navigating
            elsewhere. Calling it again with the same company merges new details.
            """
            added, merged = self.add_companies(companies)
            logger.info(
                "Recorded companies: %d new, %d merged, %d total",
                added, merged, len(self._companies),
            )
            return json.dumps({
                "ok": True,
                "new": added,
                "merged": merged,
                "total_recorded": len(self._companies),
            })

        @function_tool(strict_mode=False)
        def report_error(message: str) -> str:
            """Record a problem that may make the portfolio incomplete."""
            self.add_error(message)
            return json.dumps({"ok": True})

        return [record_fund, record_companies, report_error]
