"""Deterministic pre-pass: find and parse the portfolio page without the LLM."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.core.browser import BrowserManager

from .heuristics import (
    _is_external_company_link as is_external_company_link,
    extract_companies,
    find_sister_sites,
    find_next_page_link,
    find_portfolio_links,
    guess_fund_name,
    has_portfolio_heading,
    is_social_url,
    looks_like_portfolio_url,
)
from .models import StartupInfo
from .recorder import website_matches_name
from .tools import BrowserTools, Evidence

logger = logging.getLogger(__name__)

# Only guards against a page that never ends: 500.co has 224 pages of 10
MAX_LISTING_PAGES = 400
MAX_PROFILE_FAILURES = 25   # in a row: the site blocks us or is down, so stop visiting


@dataclass
class PrepassResult:
    fund_name_hint: str | None = None
    portfolio_url: str | None = None            # the first portfolio page found
    portfolio_urls: list[str] = field(default_factory=list)  # all of them (e.g. one per regional site)
    companies: list[StartupInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _locate_portfolio(browser: BrowserManager, html: str, page_url: str) -> str | None:
    """Portfolio page URL for the site that's open (navigating to it), or None."""
    if looks_like_portfolio_url(page_url):
        return page_url
    # Prefer a dedicated portfolio page over a "featured companies" homepage section
    links = find_portfolio_links(html, page_url)
    if links:
        browser.go_to(links[0])
        return browser.page.url
    return page_url if has_portfolio_heading(html) else None


def _read_listing(
    browser: BrowserManager, page_url: str, read_html, notes: list[str] | None = None,
) -> list[StartupInfo]:
    """Companies on the open portfolio page, expanding it and following pagination (a link
    or a "Next" control with none). Stops at a page with nothing new: a site's own "next"
    can loop, or pages can repeat the same featured companies."""
    notes = [] if notes is None else notes
    companies: list[StartupInfo] = []
    seen: set[str] = set()
    visited = {page_url}
    start_url = page_url
    for _ in range(MAX_LISTING_PAGES):
        expanded = browser.expand_listing()
        html = read_html()
        found = extract_companies(html, page_url)
        logger.info(
            "Pre-pass: %d companies on %s (scrolls=%d, clicks=%d)",
            len(found), page_url, expanded["scrolls"], expanded["clicks"],
        )
        if expanded.get("limit_hit"):
            notes.append(f"Stopped expanding {page_url} at the scroll or click limit; the list may be incomplete.")
        new = [c for c in found if c.name.lower() not in seen]
        if not new and companies:
            return companies
        seen.update(c.name.lower() for c in new)
        companies.extend(new)
        next_url = find_next_page_link(html, page_url, visited)
        if next_url:
            browser.go_to(next_url)
            page_url = browser.page.url
            visited.add(page_url)
        elif not browser.click_next_page():
            return companies
    notes.append(f"Stopped at {MAX_LISTING_PAGES} pages of {start_url}; the list may be incomplete.")
    return companies


def website_from_profile(links: list[dict[str, str]], name: str, base_url: str) -> str | None:
    """The company's own site among the links of a fund's page about it: another site, not a
    social network, named like the company (a press article's site isn't)."""
    for link in links:
        href = link.get("href", "")
        if (is_external_company_link(href, base_url) and not is_social_url(href)
                and website_matches_name(name, href)):
            return href
    return None


def fill_websites_from_profiles(browser: BrowserManager, companies: list[StartupInfo], notes: list[str]) -> int:
    """Visit the fund's page about each company with no website on its card and take the
    website from there (ycombinator.com/companies/x, sosv.com/company/x). Returns how many."""
    filled = failures = 0
    for i, company in enumerate(companies):
        if company.website or not company.profile_url:
            continue
        try:
            page = browser.go_to(company.profile_url)
        except Exception as exc:
            logger.info("Pre-pass: profile of %s failed: %s", company.name, type(exc).__name__)
            failures += 1
            if failures >= MAX_PROFILE_FAILURES:
                notes.append(f"Stopped visiting company pages after {failures} failures in a row; "
                             f"some websites are missing.")
                return filled
            continue
        failures = 0
        if website := website_from_profile(page.links, company.name, company.profile_url):
            companies[i] = company.model_copy(update={"website": website})
            filled += 1
    return filled


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

    portfolio = _locate_portfolio(browser, html, page_url)
    if portfolio:
        result.portfolio_urls.append(portfolio)
        result.companies.extend(_read_listing(browser, portfolio, read_html, result.notes))
        if not result.companies and portfolio != page_url:
            # The portfolio page can show nothing (kamayventures.com/portfolio asks for a key)
            # while the page that links to it lists the companies
            browser.go_to(page_url)
            result.companies.extend(_read_listing(browser, page_url, read_html, result.notes))
            if result.companies:
                result.portfolio_urls = [page_url]
    else:
        # No portfolio here: the fund may keep it on regional sites with their
        # own domains
        sisters = find_sister_sites(html, page_url)
        if sisters:
            logger.info("Pre-pass: no portfolio on %s; trying sister sites %s", page_url, sisters)
        for site in sisters:
            try:
                browser.go_to(site)
                site_portfolio = _locate_portfolio(browser, read_html(), browser.page.url)
                if site_portfolio:
                    result.portfolio_urls.append(site_portfolio)
                    result.companies.extend(_read_listing(browser, site_portfolio, read_html, result.notes))
            except Exception as exc:
                result.notes.append(f"Sister site {site} failed: {type(exc).__name__}: {exc}")
        if not result.portfolio_urls:
            result.notes.append("No portfolio page or portfolio section found.")
            return result

    result.portfolio_url = result.portfolio_urls[0]
    if not result.companies:
        result.notes.append(f"Could not parse a company grid on {', '.join(result.portfolio_urls)}.")
    filled = fill_websites_from_profiles(browser, result.companies, result.notes)
    logger.info("Pre-pass: %d websites taken from the fund's company pages", filled)
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
