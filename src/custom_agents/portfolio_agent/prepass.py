"""Deterministic pre-pass: find and parse the portfolio page without the LLM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.core.browser import BrowserManager

from .heuristics import (
    extract_companies,
    find_next_page_link,
    find_portfolio_links,
    guess_fund_name,
    has_portfolio_heading,
    looks_like_portfolio_url,
)
from .models import StartupInfo
from .tools import BrowserTools, Evidence

logger = logging.getLogger(__name__)

MAX_LISTING_PAGES = 5


@dataclass
class PrepassResult:
    fund_name_hint: str | None = None
    portfolio_url: str | None = None
    companies: list[StartupInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _prepass_sync(
    browser: BrowserManager, url: str, evidence: Evidence | None = None,
) -> PrepassResult:
    """evidence: if given, every page read is logged so the agent may cite it."""
    def read_html() -> str:
        html = browser.get_page_content()
        if evidence is not None:
            evidence.add(html, is_page=True, url=browser.page.url)
        return html

    result = PrepassResult()
    browser.go_to(url)
    page_url = browser.page.url
    html = read_html()
    result.fund_name_hint = guess_fund_name(html)

    # Prefer a dedicated portfolio page over a "featured companies" homepage section
    if not looks_like_portfolio_url(page_url):
        links = find_portfolio_links(html, page_url)
        if links:
            browser.go_to(links[0])
            page_url = browser.page.url
        elif not has_portfolio_heading(html):
            result.notes.append("No portfolio page or portfolio section found.")
            return result
    result.portfolio_url = page_url

    visited = {page_url}
    for _ in range(MAX_LISTING_PAGES):
        expanded = browser.expand_listing()
        html = read_html()
        found = extract_companies(html, page_url)
        logger.info(
            "Pre-pass: %d companies on %s (scrolls=%d, clicks=%d)",
            len(found), page_url, expanded["scrolls"], expanded["clicks"],
        )
        result.companies.extend(found)
        next_url = find_next_page_link(html, page_url, visited)
        if not next_url:
            break
        browser.go_to(next_url)
        page_url = browser.page.url
        visited.add(page_url)

    if not result.companies:
        result.notes.append(f"Could not parse a company grid on {result.portfolio_url}.")
    return result


async def run_prepass(browser_tools: BrowserTools, url: str) -> PrepassResult:
    """Run the pre-pass on the agent's browser. Never raises; failures become notes."""
    try:
        return await browser_tools.run_with_browser(
            lambda b: _prepass_sync(b, url, browser_tools.evidence)
        )
    except Exception as exc:
        logger.warning("Pre-pass failed: %s: %s", type(exc).__name__, exc)
        return PrepassResult(notes=[f"Pre-pass failed: {type(exc).__name__}: {exc}"])
