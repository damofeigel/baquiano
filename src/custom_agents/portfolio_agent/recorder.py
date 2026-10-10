"""Record-keeping tools for the portfolio agent.

Companies are stored in Python as the agent finds them, so a run that hits
max_turns or a model error still returns everything recorded up to that point.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol
from urllib.parse import urlparse

from agents import FunctionTool, function_tool
from pydantic import ValidationError

from .models import FinderResult, StartupInfo

logger = logging.getLogger(__name__)


# Flat, inline schema: small models fill this in far more reliably than the
# $defs/$ref/anyOf schema Pydantic generates for list[StartupInfo].
_COMPANY_FIELDS = {
    "name": "Company name as shown on the page",
    "website": "Company's own website URL, if linked",
    "description": "One sentence on what the company does",
    "sector": "e.g. biotechnology, AI, agtech, energy",
    "country": "Only if explicitly stated",
}
RECORD_COMPANIES_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "description": "A JSON array (never a string) of at most 15 companies",
            "items": {
                "type": "object",
                "properties": {
                    field: {"type": "string", "description": desc}
                    for field, desc in _COMPANY_FIELDS.items()
                },
                "required": ["name"],
            },
        },
    },
    "required": ["companies"],
}

# report_error messages that are really the model saying it has finished
_NOT_ERRORS = {"done", "finished", "complete", "completed", "none", "no errors", "ok", "n/a"}


def parse_companies_args(raw: str) -> tuple[list[StartupInfo], list[str]]:
    """Leniently parse record_companies arguments. Returns (companies, problems).

    Accepts {"companies": [...]}, a bare list, a single company object,
    companies given as a JSON string, and plain-string items (taken as names).
    """
    try:
        data: Any = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return [], [f"Arguments are not valid JSON: {exc}"]

    if isinstance(data, dict):
        data = data.get("companies", data if "name" in data else [])
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = [data]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [], ["'companies' must be a list of objects with at least a 'name'."]

    companies: list[StartupInfo] = []
    problems: list[str] = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            problems.append(f"Item {i} is not an object.")
            continue
        try:
            companies.append(StartupInfo.model_validate(item))
        except ValidationError as exc:
            problems.append(f"Item {i} ({item.get('name', '?')}): {exc.errors()[0]['msg']}")
    return companies, problems


TOOL_NAMES = {"record_fund", "record_companies", "remove_companies", "report_error", "finish"}


def looks_like_broken_tool_call(text: str) -> bool:
    """True when a final reply is really a tool call vLLM could not parse."""
    stripped = text.lstrip()
    return stripped.startswith(("{", "[", "<|python_tag|>")) and '"name"' in stripped


def salvage_companies(text: str) -> list[StartupInfo]:
    """Pull complete company objects out of a cut-off record_companies call.

    Small models often send the list as an escaped JSON string; when the reply
    is truncated at max_tokens, every company before the cut is still intact.
    """
    if "record_companies" not in text:
        return []
    if re.search(r'"companies"\s*:\s*"', text):
        text = text.replace('\\"', '"')
    decoder = json.JSONDecoder()
    found: list[StartupInfo] = []
    i = text.find("{", 1)
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        name = obj.get("name") if isinstance(obj, dict) else None
        if isinstance(name, str) and name not in TOOL_NAMES:
            try:
                found.append(StartupInfo.model_validate(obj))
            except ValidationError:
                pass
        i = text.find("{", end)
    return found


def _dedupe_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.") or None


class EvidenceLike(Protocol):
    pages_read: int

    def contains(self, text: str) -> bool: ...


class PortfolioRecorder:
    """Accumulates fund identity, companies and errors across one agent run."""

    def __init__(self, evidence: EvidenceLike | None = None) -> None:
        # When set, the record tools only accept names that appear in page
        # text the browser returned. None disables the check (pre-pass, tests).
        self.evidence = evidence
        self.tools = self._build_tools()
        self.reset()

    def reset(self) -> None:
        self.fund_name: str | None = None
        self.fund_name_hint: str | None = None  # used when the agent never calls record_fund
        self.source_url: str | None = None
        self.errors: list[str] = []
        self.finished = False
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

    def remove_companies(self, names: list[str]) -> list[str]:
        """Remove companies by name. Returns the names that were removed."""
        removed = []
        for name in names:
            company = self._companies.pop(_dedupe_key(name), None)
            if company is not None:
                removed.append(company.name)
        return removed

    def add_error(self, message: str) -> None:
        message = message.strip()
        if message.lower().strip(" .!") in _NOT_ERRORS:
            return
        if message and message not in self.errors:
            self.errors.append(message)

    def build_result(self, fallback_url: str) -> FinderResult:
        errors = list(self.errors)
        fund_name = self.fund_name or self.fund_name_hint
        if not fund_name:
            errors.append("Fund name was not identified.")
        return FinderResult(
            source_url=self.source_url or fallback_url,
            fund_name=fund_name or "Unknown",
            startups=[c.model_copy() for c in self.companies],
            errors=errors,
        )

    def check_evidence(self, companies: list[StartupInfo]) -> tuple[list[StartupInfo], list[str]]:
        """Keep companies whose name appears on a page that was read.

        Websites not seen on any page are dropped rather than rejecting the
        company. Returns (accepted, problems).
        """
        if self.evidence is None:
            return companies, []
        if self.evidence.pages_read == 0:
            return [], ["No page has been read yet. Read the portfolio page first, "
                        "then record only companies listed on it."]
        accepted, problems = [], []
        for company in companies:
            if not self.evidence.contains(company.name):
                problems.append(
                    f"'{company.name}' does not appear on any page you have read. "
                    f"Record only companies you actually saw."
                )
                continue
            domain = _domain(company.website)
            if domain and not self.evidence.contains(domain):
                logger.info("Dropping unseen website %s for %s", company.website, company.name)
                company = company.model_copy(update={"website": None})
            accepted.append(company)
        return accepted, problems

    def _build_tools(self) -> list[Any]:
        @function_tool(strict_mode=False)
        def record_fund(fund_name: str, portfolio_url: str = "") -> str:
            """Save the fund's name and the URL of its portfolio page."""
            if self.evidence is not None and not self.evidence.contains(fund_name):
                return json.dumps({
                    "ok": False,
                    "error": f"'{fund_name}' does not appear on any page you have read. "
                             f"Read the fund's site first.",
                })
            self.set_fund(fund_name, portfolio_url or None)
            logger.info("Recorded fund: %s (%s)", self.fund_name, self.source_url)
            return json.dumps({"ok": True, "fund_name": self.fund_name})

        async def _record_companies(ctx: Any, raw_args: str) -> str:
            companies, problems = parse_companies_args(raw_args)
            companies, unseen = self.check_evidence(companies)
            problems += unseen
            added, merged = self.add_companies(companies)
            logger.info(
                "Recorded companies: %d new, %d merged, %d total, %d problem(s)",
                added, merged, len(self._companies), len(problems),
            )
            if problems:
                logger.warning("record_companies problems: %s | raw args: %.500s", problems, raw_args)
            return json.dumps({
                "ok": not problems,
                "new": added,
                "merged": merged,
                "total_recorded": len(self._companies),
                **({"problems": problems} if problems else {}),
            }, ensure_ascii=False)

        record_companies = FunctionTool(
            name="record_companies",
            description=(
                "Save portfolio companies found on the page you just read. Call it "
                "right after reading each portfolio page, with at most 15 companies "
                "per call; call it again for the rest. Repeating a company merges "
                "new details."
            ),
            params_json_schema=RECORD_COMPANIES_SCHEMA,
            on_invoke_tool=_record_companies,
            strict_json_schema=False,
        )

        @function_tool(strict_mode=False)
        def remove_companies(names: list[str], reason: str) -> str:
            """Remove recorded entries that are not portfolio companies.

            Use only for entries that are clearly wrong, e.g. team members,
            partners, LPs or news items.
            """
            removed = self.remove_companies(names)
            logger.warning("Removed companies %s — reason: %s", removed, reason)
            return json.dumps({
                "ok": True,
                "removed": removed,
                "not_found": [
                    n for n in names
                    if _dedupe_key(n) not in {_dedupe_key(r) for r in removed}
                ],
                "total_recorded": len(self._companies),
            })

        @function_tool(strict_mode=False)
        def report_error(message: str) -> str:
            """Record a problem that may make the portfolio incomplete."""
            self.add_error(message)
            return json.dumps({"ok": True})

        @function_tool(strict_mode=False)
        def finish(summary: str = "") -> str:
            """Call when the portfolio listing is complete. Ends the run."""
            if self.evidence is not None and self.evidence.pages_read == 0:
                return ("Not finished: you have not read any page yet. "
                        "Use navigate_to_url on the fund's website first.")
            self.finished = True
            logger.info("Agent finished: %s", summary)
            return "finished"

        return [record_fund, record_companies, remove_companies, report_error, finish]
