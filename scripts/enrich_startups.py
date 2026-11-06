#!/usr/bin/env python3
"""
enrich_startups.py — Visit each startup's website and classify it with the LLM.

Usage:
    python scripts/enrich_startups.py                        # latest output/startups_merged_*.json
    python scripts/enrich_startups.py output/startups_merged_<time>.json --limit 20
    python scripts/enrich_startups.py --all                  # also startups known to be outside Argentina
    python scripts/enrich_startups.py --names "Puna Bio" Beeflow

For each startup: read its homepage and one about/contact page, look for
Argentina signals (.ar domain, +54 phone, Argentine cities, a fund that is an
Argentine programme), then make one structured LLM call: country, year founded,
summary, deep tech yes/no and a technology area from scripts/taxonomy.json (edit
it to change the categories). A startup with no website to read and no fund text
isn't sent to the model: its deep tech answer stays unknown (null).

The model must quote the page text behind the country and the founding year; a
quote that isn't on the page is discarded, and so is one that doesn't show where
the company is (a lone city name, a job title, a press outlet). A country that
a fund states or a strong signal goes in country_likely. By default only startups whose
country is Argentina or unknown are processed. Progress is saved after every
startup and a rerun skips the ones already done.

Writes output/startups_enriched_<time>.json (named after the merged file).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core import config  # noqa: E402
from src.core.matching import company_domain, normalize_country, web_url, website_domain  # noqa: E402
from src.core.structured_llm import (  # noqa: E402,F401  (re-exported for tests)
    Classifier, check_llm, make_llm_classifier, parse_json, quote_found,
)

OUTPUT_DIR = PROJECT_ROOT / "output"
TAXONOMY_FILE = Path(__file__).resolve().parent / "taxonomy.json"
HOME_CHARS, ABOUT_CHARS = 6_000, 4_000

ABOUT_LINK = re.compile(
    r"\b("
    r"about(?:[-_\s]us)?|who[-_\s]we[-_\s]are|our[-_\s]story|company|team|contact(?:[-_\s]us)?|get[-_\s]in[-_\s]touch|"
    r"nosotros|qui[eé]nes(?:[-_\s]somos)?|sobre(?:[-_\s]nosotros)?|acerca(?:[-_\s]de)?|empresa|equipo|contacto|"
    r"con[oó]cenos|(?:nuestra[-_\s])?historia|"
    r"quem[-_\s]somos|sobre[-_\s]n[oó]s|equipe|contato|nossa[-_\s]hist[oó]ria"
    r")\b",
    re.IGNORECASE,
)

PROGRAMME_SIGNAL = "Argentine programme"
ARGENTINA_SIGNALS = {
    "phone +54": re.compile(r"\+\s?54[\s\d-]{6,}"),
    "mentions Argentina": re.compile(r"\bargentin[ae]\b|\bargentina\b", re.IGNORECASE),
    "Argentine city": re.compile(
        r"\b(buenos aires|caba|c[oó]rdoba|rosario|mendoza|la plata|tucum[aá]n|bariloche|"
        r"mar del plata|neuqu[eé]n|salta|santa fe|bah[ií]a blanca)\b",
        re.IGNORECASE,
    ),
}

#  Reading the startup's site 

def collect_pages(browser: Any, website: str) -> tuple[list[dict[str, str]], str]:
    """Homepage plus one about/contact page. Returns (pages, status)."""
    url = web_url(website)                        # funds often list "www.kigui.io"; a browser needs https://
    if not url or not website_domain(url):
        return [], f"not a website: {website.strip()[:80]}"                 # tel:, an email, "https://[x"
    website = url
    try:
        home = browser.go_to(website)
    except Exception as exc:
        detail = (str(exc).strip().splitlines() or [""])[0][:160]
        return [], f"unreachable: {type(exc).__name__}" + (f": {detail}" if detail else "")
    pages = [{"url": home.url or website, "text": home.plain_text[:HOME_CHARS]}]
    if not home.plain_text.strip():
        return pages, "empty"
    base = website_domain(home.url or website)
    for link in home.links:
        href = link.get("href", "")
        if website_domain(href) != base or href.rstrip("/") == (home.url or "").rstrip("/"):
            continue
        if ABOUT_LINK.search(link.get("text", "")) or ABOUT_LINK.search(urlparse(href).path):
            try:
                about = browser.go_to(href)
                pages.append({"url": about.url or href, "text": about.plain_text[:ABOUT_CHARS]})
            except Exception:
                pass
            break
    return pages, "ok"


def _is_ar(url: str | None) -> bool:
    return (website_domain(url) or "").endswith(".ar")      # also "empresa.com.ar", with no https://


# Argentine programmes whose own site isn't under .ar (the rest are recognized by their domain)
ARGENTINE_PROGRAMME_SITES = {"sf500.vc"}


def _is_argentine_programme(url: str | None) -> bool:
    domain = website_domain(url) or ""
    return domain.endswith(".ar") or domain in ARGENTINE_PROGRAMME_SITES


def argentina_signals(website: str | None, texts: list[str], landed: str | None = None,
                      sources: list[str] | None = None) -> list[str]:
    """Signals of an Argentine company. ".ar domain" counts for the recorded website or for
    LANDED, the page the browser ended on (neworgans.com redirecting to neworgans.com.ar).
    SOURCES are the portfolio pages that list it: corlab.cordoba.gob.ar lists Córdoba's startups."""
    website = (website or "").strip()
    signals = []
    if _is_ar(website) or _is_ar(landed):
        signals.append(".ar domain")
    if any(_is_argentine_programme(source) for source in sources or []):
        signals.append(PROGRAMME_SIGNAL)
    joined = "\n".join(texts)
    for label, pattern in ARGENTINA_SIGNALS.items():
        if pattern.search(joined):
            signals.append(label)
    return signals


#  The LLM call

def result_schema(taxonomy: dict[str, Any]) -> dict[str, Any]:
    nullable = lambda t: {"type": [t, "null"]}  # noqa: E731
    return {
        "type": "object",
        "properties": {
            "country": nullable("string"),
            "country_evidence": nullable("string"),
            "year_founded": nullable("integer"),
            "year_evidence": nullable("string"),
            "summary": {"type": "string"},
            "deep_tech": {"type": "boolean"},
            "deep_tech_reason": {"type": "string"},
            "tech_area": {"type": "string", "enum": list(taxonomy["areas"])},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["country", "country_evidence", "year_founded", "year_evidence", "summary",
                     "deep_tech", "deep_tech_reason", "tech_area", "confidence"],
        "additionalProperties": False,
    }


def fund_labels(startup: dict) -> list[str]:
    """The funds' own category labels (merged 'sector', all of them when funds disagree)."""
    return startup.get("conflicts", {}).get("sector") or ([startup["sector"]] if startup.get("sector") else [])


def build_messages(startup: dict, pages: list[dict[str, str]], signals: list[str],
                   taxonomy: dict[str, Any]) -> list[dict[str, str]]:
    areas = "\n".join(f"- {key}: {a['label']} ({'deep tech' if a['deep_tech'] else 'not deep tech'})"
                      for key, a in taxonomy["areas"].items())
    fund_texts = "\n".join(f"- {d['fund']}: {d['text']}" for d in startup.get("descriptions", []))
    labels = ", ".join(fund_labels(startup)) or "none"
    page_texts = "\n\n".join(f"### {p['url']}\n{p['text']}" for p in pages) or "(website not available)"
    system = f"""You classify startups for a database of the Argentine deep tech ecosystem.
Use only the texts given. Never guess.

Deep tech: {taxonomy['deep_tech_definition']}

Technology areas (tech_area must be one of these keys):
{areas}

Rules:
- country: where the company is headquartered or was founded, only if a text states it.
  country_evidence: copy the exact words from the texts that show it (an address,
  "based in ...", "Argentine company"). If no text states it, both are null. A city name
  alone, a job title ("Head of Sales Argentina"), a press outlet ("Forbes Argentina") or a
  market it sells in doesn't state it.
- year_founded: only if a text states it ("founded in 2019", "since 2019");
  year_evidence: the exact words. Otherwise both null. A copyright year is not a founding year.
- summary: one sentence on what the company does.
- deep_tech: follow the definition above, and it must agree with the tech_area you pick;
  deep_tech_reason: one sentence why.
- confidence: low when the texts say little about the company."""
    user = f"""Startup: {startup['name']}
Website: {startup.get('website') or 'none'}
Signals found automatically: {', '.join(signals) or 'none'}

How investment funds describe it:
{fund_texts or '(none)'}
Category labels funds give it (their own wording, a hint only): {labels}

Its website:
{page_texts}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


#  Checking the answer 

def verify(result: dict[str, Any], texts: list[str], taxonomy: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for field, evidence in (("country", "country_evidence"), ("year_founded", "year_evidence")):
        if result.get(field) is not None and not quote_found(result.get(evidence), texts):
            checks.append(f"{field} dropped: evidence not found on the pages")
            result[field] = None
            result[evidence] = None
    year = result.get("year_founded")
    if year is not None and not 1950 <= int(year) <= datetime.now().year:
        checks.append(f"year_founded dropped: {year} is implausible")
        result["year_founded"] = result["year_evidence"] = None
    area = taxonomy["areas"].get(result.get("tech_area"), {})
    if area and area["deep_tech"] != result.get("deep_tech"):
        checks.append(f"deep_tech={result.get('deep_tech')} but area {result['tech_area']} is "
                      f"{'deep tech' if area['deep_tech'] else 'not deep tech'} in the taxonomy")
    result["checks"] = checks
    return result


# Evidence that a place is mentioned, not that the company is there
_AMBIGUOUS_PLACE = re.compile(
    r"^\W*(c[oó]rdoba|rosario|mendoza|la plata|santa fe|salta|tucum[aá]n|caba|neuqu[eé]n|bariloche|"
    r"mar del plata|bah[ií]a blanca)\W*$", re.IGNORECASE,
)
_JOB_TITLE = re.compile(r"\b(head of|manager|director|lead|representative|sales|ventas|gerente)\b", re.IGNORECASE)
_PRESS_OUTLET = re.compile(r"\b(forbes|la naci[oó]n|clar[ií]n|infobae|bloomberg|techcrunch)\b", re.IGNORECASE)


def weak_country_evidence(evidence: str | None) -> str | None:
    """Why a quote doesn't show where a company is, or None. A lone "Córdoba" may be in Spain,
    and "Head of Sales Argentina" or "Forbes Argentina" only show a market or a press mention."""
    text = evidence or ""
    if _AMBIGUOUS_PLACE.match(text):
        return "a city name alone"
    if _JOB_TITLE.search(text):
        return "a job title"
    if _PRESS_OUTLET.search(text):
        return "a press outlet"
    return None


def recheck_country(enrichment: dict[str, Any]) -> dict[str, Any]:
    """Drop a country whose evidence doesn't show where the company is. No page is needed,
    so results stored before this rule are brought up to date too."""
    why = weak_country_evidence(enrichment.get("country_evidence")) if enrichment.get("country") else None
    if why:
        enrichment["checks"] = [*enrichment.get("checks", []), f"country dropped: the evidence is only {why}"]
        enrichment["country"] = enrichment["country_evidence"] = None
    return enrichment


# Signals strong enough to say "likely Argentine" when no text states the country
STRONG_SIGNALS = {".ar domain", "phone +54", PROGRAMME_SIGNAL}


def add_hints(enrichment: dict[str, Any], fund_country: str | None = None) -> dict[str, Any]:
    """One spelling per country, and country_likely: the stated country, else the one a fund
    gives (FUND_COUNTRY), else "Argentina" when the site has a .ar domain or a +54 phone number
    or an Argentine programme lists it. country itself stays as checked against the pages."""
    enrichment["country"] = normalize_country(enrichment.get("country"))
    strong = STRONG_SIGNALS & set(enrichment.get("argentina_signals") or [])
    enrichment["country_likely"] = (enrichment["country"] or normalize_country(fund_country)
                                    or ("Argentina" if strong else None))
    return enrichment


def no_evidence(status: str, signals: list[str]) -> dict[str, Any]:
    """The result for a startup with nothing to read: unknown, which is not "not deep tech"."""
    return {
        "country": None, "country_evidence": None, "year_founded": None, "year_evidence": None,
        "summary": None, "deep_tech": None, "deep_tech_reason": "Nothing to read: no website and no fund text.",
        "tech_area": "unknown", "confidence": "low", "checks": [], "argentina_signals": signals,
        "website_status": status, "pages_read": [],
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def had_nothing_to_read(entry: dict[str, Any]) -> bool:
    enrichment = entry["enrichment"]
    return bool(enrichment.get("website_status")) and not enrichment.get("pages_read") and not entry.get("descriptions")


def update_stored(entry: dict[str, Any]) -> None:
    """Apply today's rules to a stored result, no website visit or LLM call needed."""
    refresh_domain_signal(entry)
    enrichment = entry["enrichment"]
    if had_nothing_to_read(entry) and enrichment.get("deep_tech") is not None:
        entry["enrichment"] = enrichment = no_evidence(enrichment.get("website_status") or "no website",
                                                        enrichment.get("argentina_signals") or [])
    add_hints(recheck_country(enrichment), entry.get("country"))


def refresh_domain_signal(entry: dict[str, Any]) -> dict[str, Any]:
    """Recompute ".ar domain" from the entry's website and the page the browser landed on (the
    first of pages_read): it needs nothing else, so a result stored before a fix (or with a
    website changed since) is brought up to date without visiting the site. The text
    signals stay as found."""
    enrichment = entry["enrichment"]
    found = [s for s in enrichment.get("argentina_signals") or [] if s not in (".ar domain", PROGRAMME_SIGNAL)]
    landed = (enrichment.get("pages_read") or [None])[0]
    enrichment["argentina_signals"] = argentina_signals(entry.get("website"), [], landed, entry.get("sources")) + found
    return entry


def enrich_one(startup: dict, browser: Any, classify: Classifier, taxonomy: dict[str, Any]) -> dict[str, Any]:
    pages, status = collect_pages(browser, startup["website"]) if startup.get("website") else ([], "no website")
    fund_texts = [d["text"] for d in startup.get("descriptions", [])]
    texts = [p["text"] for p in pages] + fund_texts
    signals = argentina_signals(startup.get("website"), [p["text"] for p in pages],
                                pages[0]["url"] if pages else None,      # after any redirect
                                startup.get("sources"))
    if not any(text.strip() for text in texts):
        return add_hints(no_evidence(status, signals), startup.get("country"))
    result = classify(build_messages(startup, pages, signals, taxonomy), result_schema(taxonomy))
    result = recheck_country(verify(result, texts, taxonomy))
    return add_hints({
        **result,
        "argentina_signals": signals,
        "website_status": status,
        "pages_read": [p["url"] for p in pages],
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, startup.get("country"))


#  Running over a merged file 

def select(startups: list[dict], include_all: bool, names: list[str]) -> list[dict]:
    if names:
        wanted = {n.casefold() for n in names}
        return [s for s in startups if s["name"].casefold() in wanted
                or any(a.casefold() in wanted for a in s.get("aliases", []))]
    if include_all:
        return startups
    return [s for s in startups if (normalize_country(s.get("country")) or "Argentina") == "Argentina"]


def reuse_previous(startups: list[dict], files: list[Path], done: dict[str, dict]) -> int:
    """Copy enrichments from earlier enriched files into DONE, for startups not done yet.

    Matched by the company's own website domain (a LinkedIn or Google Sites link
    identifies nobody), else by name (any alias), but never across two different
    domains: "Nova" at nova.com is not "Nova" at nova.com.ar. Returns how many were reused."""
    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for path in files:
        for old in json.loads(path.read_text(encoding="utf-8"))["startups"]:
            if not old.get("enrichment"):
                continue
            if domain := company_domain(old.get("website")):
                by_domain.setdefault(domain, old)
            for alias in [old["name"], *old.get("aliases", [])]:
                by_name.setdefault(alias.casefold(), old)
    reused = 0
    for startup in startups:
        if startup["name"] in done:
            continue
        domain = company_domain(startup.get("website"))
        found = by_domain.get(domain) if domain else None
        if found is None:
            for alias in [startup["name"], *startup.get("aliases", [])]:
                candidate = by_name.get(alias.casefold())
                old_domain = company_domain(candidate.get("website")) if candidate else None
                if candidate and not (domain and old_domain and domain != old_domain):
                    found = candidate
                    break
        if found and startup.get("website") and found["enrichment"].get("website_status") == "no website":
            found = None                # now there is a website to read, which the old result didn't have
        if found:
            # A full copy per startup: two startups can match one old record
            done[startup["name"]] = {**startup, "enrichment": copy.deepcopy(found["enrichment"])}
            reused += 1
    return reused


def latest_merged() -> Path | None:
    files = sorted(OUTPUT_DIR.glob("startups_merged_*.json"))
    return files[-1] if files else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Visit startup websites and classify them with the LLM.")
    parser.add_argument("merged", nargs="?", type=Path, help="Merged file (default: latest output/startups_merged_*.json)")
    parser.add_argument("--all", action="store_true", help="Also startups known to be outside Argentina")
    parser.add_argument("--names", nargs="+", default=[], help="Only these startups")
    parser.add_argument("--limit", type=int, help="Process at most N startups this run")
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_FILE)
    parser.add_argument("--out", type=Path, help="Output file (default: output/startups_enriched_<time>.json)")
    parser.add_argument("--previous", type=Path, nargs="+", default=[],
                        help="Earlier enriched files: reuse their results (matched by name or website)")
    args = parser.parse_args()

    merged_path = args.merged or latest_merged()
    if not merged_path or not merged_path.exists():
        sys.exit("No merged file. Run scripts/merge_portfolios.py first.")
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    out = args.out or OUTPUT_DIR / merged_path.name.replace("startups_merged_", "startups_enriched_")

    # Resume: keep what a previous run of the same output already did
    done: dict[str, dict] = {}
    if out.exists():
        done = {s["name"]: s for s in json.loads(out.read_text(encoding="utf-8"))["startups"] if s.get("enrichment")}
    reused = reuse_previous(merged["startups"], args.previous, done)
    for entry in done.values():
        update_stored(entry)
    todo = [s for s in select(merged["startups"], args.all, args.names) if s["name"] not in done]
    if args.limit is not None:
        todo = todo[: args.limit]
    print(f"{len(done)} already enriched ({reused} from earlier files), {len(todo)} to do -> {out}")

    result = {"enriched_at": None, "source": str(merged_path), "taxonomy": str(args.taxonomy), "startups": []}

    def save() -> None:
        startups = [done.get(s["name"], s) for s in merged["startups"]]
        result["enriched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["startups"] = startups
        result["summary"] = summarize(startups)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not todo:
        save()
        print(json.dumps(result["summary"], indent=2))
        return

    import openai
    from src.core.browser import BrowserManager

    client = openai.OpenAI(base_url=config.VLLM_BASE_URL, api_key=config.API_KEY, max_retries=1)
    if problem := check_llm(client, config.VLLM_MODEL):
        sys.exit(problem)
    classify = make_llm_classifier(client, config.VLLM_MODEL)
    # Errors that will fail every startup the same way: stop instead of
    # visiting every website for nothing (progress is saved; a rerun resumes)
    fatal = (openai.APIConnectionError, openai.AuthenticationError, openai.PermissionDeniedError,
             openai.NotFoundError, openai.BadRequestError)

    with BrowserManager() as browser:
        for i, startup in enumerate(todo, 1):
            try:
                enrichment = enrich_one(startup, browser, classify, taxonomy)
            except fatal as exc:
                save()
                sys.exit(f"[{i}/{len(todo)}] {startup['name']}: stopping, the LLM server failed "
                         f"({type(exc).__name__}: {exc}). Progress saved; rerun to continue.")
            except Exception as exc:
                print(f"[{i}/{len(todo)}] {startup['name']}: FAILED {type(exc).__name__}: {exc}")
                continue
            done[startup["name"]] = {**startup, "enrichment": enrichment}
            save()
            verdict = {True: "deep tech", False: "not deep tech", None: "unknown"}[enrichment.get("deep_tech")]
            print(f"[{i}/{len(todo)}] {startup['name']}: {enrichment.get('country') or '?'}, {verdict} "
                  f"({enrichment.get('tech_area')}, {enrichment.get('confidence')})"
                  + (f"  checks: {'; '.join(enrichment['checks'])}" if enrichment["checks"] else ""))
    save()
    print(json.dumps(result["summary"], indent=2))


def summarize(startups: list[dict]) -> dict[str, Any]:
    enriched = [s["enrichment"] for s in startups if s.get("enrichment")]
    argentine = [e for e in enriched if e.get("country") == "Argentina"]
    likely = [e for e in enriched if not e.get("country") and e.get("country_likely") == "Argentina"]
    return {
        "enriched": len(enriched),
        "argentina": len(argentine),
        "argentina_deep_tech": sum(1 for e in argentine if e.get("deep_tech")),
        "argentina_likely": len(likely),
        "argentina_likely_deep_tech": sum(1 for e in likely if e.get("deep_tech")),
        "deep_tech": sum(1 for e in enriched if e.get("deep_tech")),
        "deep_tech_unknown": sum(1 for e in enriched if e.get("deep_tech") is None),
        "deep_tech_contradicts_area": sum(1 for e in enriched if any("in the taxonomy" in c for c in e.get("checks", []))),
        "by_area": dict(Counter(e.get("tech_area") for e in enriched).most_common()),
        "country_unknown": sum(1 for e in enriched if not e.get("country")),
    }


if __name__ == "__main__":
    main()
