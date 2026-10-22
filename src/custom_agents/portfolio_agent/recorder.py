"""Record-keeping tools for the portfolio agent.

Companies are stored in Python as the agent finds them, so a run that hits
max_turns or a model error still returns everything recorded up to that point.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Protocol
from urllib.parse import urlparse

from agents import FunctionTool, function_tool
from pydantic import ValidationError

from .heuristics import is_social_url
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


def _same_company_name(a: str, b: str) -> bool:
    """Name variants of one company, e.g. from a fund's regional sites:
    "Heartbest" / "Heartbest Foods", "Botanical Solution" / "Botanical Solutions".

    The longer name must be the shorter plus whole extra words (no digits, so
    "Startup 1" / "Startup 10" and "Glaia" / "Glaia 2" stay apart), and the
    shorter needs 6+ characters so short names ("Agree" / "Agreena") stay apart.
    """
    (short_key, short), (long_key, long) = sorted(
        ((_dedupe_key(a), a), (_dedupe_key(b), b)), key=lambda pair: len(pair[0])
    )
    if not short_key:
        return False
    if short_key.rstrip("s") == long_key.rstrip("s") and short_key[-1:].isalpha():
        return True
    short_words, long_words = short.lower().split(), long.lower().split()
    extra = long_words[len(short_words):]
    return (
        len(short_key) >= 6
        and long_words[:len(short_words)] == short_words
        and bool(extra)
        and all(word.isalpha() for word in extra)
    )


def _dedupe_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.") or None


def _same_site(a: str | None, b: str | None) -> bool:
    da, db = _domain(a), _domain(b)
    return bool(da and db and (da == db or da.endswith("." + db) or db.endswith("." + da)))


def _ascii_key(text: str) -> str:
    """Lowercase letters and digits only, accents removed ("BioClé" -> "biocle")."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Domain endings stripped before comparing a domain with a company name
_DOMAIN_ENDINGS = {
    "com", "net", "org", "io", "co", "ai", "bio", "tech", "app", "dev", "info",
    "life", "xyz", "health", "earth", "farm", "ar", "uy", "cl", "br", "mx", "es",
}


def website_matches_name(name: str, website: str) -> bool:
    """True if the website's main domain label plausibly belongs to the name.

    They must share a distinctive chunk: 5 characters, or the whole of a
    shorter name/label ("Eywa" ~ eywa.bio). Catches websites taken from a
    neighbouring company ("Bioplastix" -> bitgenia.com) while keeping brand
    variants ("Algaebio+" -> algaebiomas.com, "Tomorrow" -> tomorrowfoods.com.ar).
    """
    labels = (_domain(website) or "").split(".")
    while len(labels) > 1 and (labels[-1] in _DOMAIN_ENDINGS or len(labels[-1]) <= 3):
        labels.pop()
    site, company = _ascii_key("".join(labels)), _ascii_key(name)
    needed = min(5, len(site), len(company))
    if needed == 0:
        return True
    match = SequenceMatcher(None, company, site, autojunk=False).find_longest_match(
        0, len(company), 0, len(site)
    )
    return match.size >= needed


# Page chrome that small models copy as company names ("JOIN US", "Visit our Linkedin profile")
_NAVIGATION_NAME = re.compile(
    r"^(join us|[uú]nete|contact( us)?|contacto|cont[aá]ctanos|follow us|s[ií]guenos|"
    r"visit\b.*|visita\b.*|learn more|read more|see more|view more|ver m[aá]s|leer m[aá]s|"
    r"apply( now)?|aplicar|postular|subscribe|suscr[ií]bete|newsletter|home|inicio|"
    r"about( us)?|nosotros|qui[eé]nes somos|portfolio|portafolio|team|equipo|blog|news|"
    r"noticias|cookie.*|privacy.*|privacidad|terms.*|t[eé]rminos.*|log ?in|sign ?up|"
    r"website|sitio web|linkedin|instagram|twitter|facebook|youtube)$",
    re.IGNORECASE,
)


def looks_like_navigation(name: str) -> bool:
    return bool(_NAVIGATION_NAME.match(name.strip()))


# finish() is refused this many times while the recorded portfolio page is
# unopened, then accepted with an error so a stuck model can't burn every turn.
MAX_FINISH_REFUSALS = 2


class EvidenceLike(Protocol):
    pages_read: int

    def contains(self, text: str) -> bool: ...

    def opened(self, url: str) -> bool: ...


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
        self.site_url: str | None = None  # the fund URL being extracted; its links aren't company sites
        self.source_url: str | None = None
        self.errors: list[str] = []
        self.finished = False
        self._finish_refusals = 0
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
            if company.website and is_social_url(company.website):
                logger.info("Dropping social-network website %s for %s", company.website, company.name)
                company = company.model_copy(update={"website": None})
            if company.website and (
                _same_site(company.website, self.site_url) or _same_site(company.website, self.source_url)
            ):
                logger.info("Dropping fund's own link %s for %s", company.website, company.name)
                company = company.model_copy(update={"website": None})
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
        for existing_key, existing in self._companies.items():
            if _same_company_name(company.name, existing.name):
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

    def saved_names_note(self, max_chars: int = 2_000) -> str:
        """Compact list of saved names, so the model knows where to continue.

        Without it, a model that lost track (e.g. after a cut-off call was
        removed from its history) starts the list over from the beginning.
        """
        names = ", ".join(c.name for c in self.companies)
        if len(names) > max_chars:
            names = names[:max_chars].rsplit(", ", 1)[0] + ", ..."
        return f"Saved so far ({len(self._companies)}): {names or 'none'}."

    def portfolio_unopened(self) -> bool:
        """True if a portfolio URL was recorded but its page was never opened."""
        return bool(
            self.evidence is not None
            and self.source_url
            and not self.evidence.opened(self.source_url)
        )

    def check_evidence(self, companies: list[StartupInfo]) -> tuple[list[StartupInfo], list[str]]:
        """Vet companies the model recorded. Returns (accepted, problems).

        Rejects page navigation used as a name and, when evidence is set,
        names that appear on no page that was read. Websites are dropped
        (company kept) when never seen on a page or when they don't plausibly
        belong to the name, e.g. taken from the neighbouring card.
        """
        if self.evidence is not None and self.evidence.pages_read == 0:
            return [], ["No page has been read yet. Read the portfolio page first, "
                        "then record only companies listed on it."]
        accepted, problems = [], []
        for company in companies:
            if looks_like_navigation(company.name):
                problems.append(f"'{company.name}' is site navigation, not a company name.")
                continue
            if self.evidence is not None and not self.evidence.contains(company.name):
                problems.append(
                    f"'{company.name}' does not appear on any page you have read. "
                    f"Record only companies you actually saw."
                )
                continue
            domain = _domain(company.website)
            if domain and self.evidence is not None and not self.evidence.contains(domain):
                logger.info("Dropping unseen website %s for %s", company.website, company.name)
                company = company.model_copy(update={"website": None})
            elif domain and not website_matches_name(company.name, company.website or ""):
                logger.info("Dropping website %s: doesn't match %s", company.website, company.name)
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
            reply: dict[str, Any] = {"ok": True, "fund_name": self.fund_name}
            if self.portfolio_unopened():
                # Small models follow the latest tool result more than the prompt
                reply["next_step"] = (
                    f"Open {self.source_url} with navigate_to_url and record "
                    f"every company listed there."
                )
            return json.dumps(reply)

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
            reply: dict[str, Any] = {
                "ok": not problems,
                "new": added,
                "merged": merged,
                "total_recorded": len(self._companies),
            }
            if problems:
                reply["problems"] = problems
            if added == 0 and merged:
                reply["note"] = (
                    "All of these were already saved. " + self.saved_names_note()
                    + " Record only companies not in this list; if none are left, call finish."
                )
            return json.dumps(reply, ensure_ascii=False)

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
            if self.portfolio_unopened():
                self._finish_refusals += 1
                if self._finish_refusals <= MAX_FINISH_REFUSALS:
                    logger.warning("finish refused: portfolio page %s not opened", self.source_url)
                    return (
                        f"Not finished: you recorded the portfolio page {self.source_url} "
                        f"but have not opened it. Open it with navigate_to_url, record "
                        f"its companies, then call finish."
                    )
                self.add_error(
                    f"The portfolio page {self.source_url} was never opened; "
                    f"the list may be incomplete."
                )
            self.finished = True
            logger.info("Agent finished: %s", summary)
            return "finished"

        return [record_fund, record_companies, remove_companies, report_error, finish]
