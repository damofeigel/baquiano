#!/usr/bin/env python3
"""
enrich_startups.py — Visit each startup's website and classify it with the LLM.

Usage:
    python scripts/enrich_startups.py                        # latest output/startups_merged_*.json
    python scripts/enrich_startups.py output/startups_merged_<time>.json --limit 20
    python scripts/enrich_startups.py --all                  # also startups known to be outside Argentina
    python scripts/enrich_startups.py --names "Puna Bio" Beeflow

For each startup: read its homepage and one about/contact page, look for
Argentina signals (.ar domain, +54 phone, Argentine cities), then make one
structured LLM call: country, year founded, summary, deep tech yes/no and a
technology area from scripts/taxonomy.json (edit it to change the categories).

The model must quote the page text behind the country and the founding year;
a quote that isn't on the page is discarded. By default only startups whose
country is Argentina or unknown are processed. Progress is saved after every
startup and a rerun skips the ones already done.

Writes output/startups_enriched_<time>.json (named after the merged file).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core import config  # noqa: E402

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

ARGENTINA_SIGNALS = {
    "phone +54": re.compile(r"\+\s?54[\s\d-]{6,}"),
    "mentions Argentina": re.compile(r"\bargentin[ae]\b|\bargentina\b", re.IGNORECASE),
    "Argentine city": re.compile(
        r"\b(buenos aires|caba|c[oó]rdoba|rosario|mendoza|la plata|tucum[aá]n|bariloche|"
        r"mar del plata|neuqu[eé]n|salta|santa fe|bah[ií]a blanca)\b",
        re.IGNORECASE,
    ),
}

Classifier = Callable[[list[dict[str, str]], dict[str, Any]], dict[str, Any]]


#  Reading the startup's site 

def collect_pages(browser: Any, website: str) -> tuple[list[dict[str, str]], str]:
    """Homepage plus one about/contact page. Returns (pages, status)."""
    try:
        home = browser.go_to(website)
    except Exception as exc:
        return [], f"unreachable: {type(exc).__name__}"
    pages = [{"url": home.url or website, "text": home.plain_text[:HOME_CHARS]}]
    if not home.plain_text.strip():
        return pages, "empty"
    base = urlparse(home.url or website).netloc.removeprefix("www.")
    for link in home.links:
        href = link.get("href", "")
        if urlparse(href).netloc.removeprefix("www.") != base or href.rstrip("/") == (home.url or "").rstrip("/"):
            continue
        if ABOUT_LINK.search(link.get("text", "")) or ABOUT_LINK.search(urlparse(href).path):
            try:
                about = browser.go_to(href)
                pages.append({"url": about.url or href, "text": about.plain_text[:ABOUT_CHARS]})
            except Exception:
                pass
            break
    return pages, "ok"


def argentina_signals(website: str | None, texts: list[str]) -> list[str]:
    signals = []
    host = urlparse(website or "").netloc.lower()
    if host.endswith(".ar") or ".ar:" in host:
        signals.append(".ar domain")
    joined = "\n".join(texts)
    for label, pattern in ARGENTINA_SIGNALS.items():
        if pattern.search(joined):
            signals.append(label)
    return signals


#  The LLM call --

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


def build_messages(startup: dict, pages: list[dict[str, str]], signals: list[str],
                   taxonomy: dict[str, Any]) -> list[dict[str, str]]:
    areas = "\n".join(f"- {key}: {a['label']}" for key, a in taxonomy["areas"].items())
    fund_texts = "\n".join(f"- {d['fund']}: {d['text']}" for d in startup.get("descriptions", []))
    page_texts = "\n\n".join(f"### {p['url']}\n{p['text']}" for p in pages) or "(website not available)"
    system = f"""You classify startups for a database of the Argentine deep tech ecosystem.
Use only the texts given. Never guess.

Deep tech: {taxonomy['deep_tech_definition']}

Technology areas (tech_area must be one of these keys):
{areas}

Rules:
- country: where the company is headquartered or was founded, only if a text states it.
  country_evidence: copy the exact words from the texts that show it (an address,
  "based in ...", "Argentine company"). If no text states it, both are null.
- year_founded: only if a text states it ("founded in 2019", "since 2019");
  year_evidence: the exact words. Otherwise both null. A copyright year is not a founding year.
- summary: one sentence on what the company does.
- deep_tech: follow the definition above; deep_tech_reason: one sentence why.
- confidence: low when the texts say little about the company."""
    user = f"""Startup: {startup['name']}
Website: {startup.get('website') or 'none'}
Signals found automatically: {', '.join(signals) or 'none'}

How investment funds describe it:
{fund_texts or '(none)'}

Its website:
{page_texts}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def make_llm_classifier(client: Any, model: str) -> Classifier:
    """Structured output via the server's JSON-schema mode, with a plain-JSON fallback."""
    def classify(messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=config.LLM_MAX_TOKENS,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "startup_enrichment", "schema": schema, "strict": True}},
        )
        return parse_json(response.choices[0].message.content or "")
    return classify


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in a reply, tolerating code fences or text around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"no JSON object in model reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


#  Checking the answer 

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def quote_found(quote: str | None, texts: list[str]) -> bool:
    """True if the quoted evidence really appears in the texts (case, accents, spacing ignored)."""
    if not quote:
        return False
    q = _norm(quote).strip(" .\"'“”…")
    return len(q) >= 4 and q in _norm("\n".join(texts))


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


def enrich_one(startup: dict, browser: Any, classify: Classifier, taxonomy: dict[str, Any]) -> dict[str, Any]:
    pages, status = collect_pages(browser, startup["website"]) if startup.get("website") else ([], "no website")
    fund_texts = [d["text"] for d in startup.get("descriptions", [])]
    texts = [p["text"] for p in pages] + fund_texts
    signals = argentina_signals(startup.get("website"), [p["text"] for p in pages])
    result = classify(build_messages(startup, pages, signals, taxonomy), result_schema(taxonomy))
    result = verify(result, texts, taxonomy)
    return {
        **result,
        "argentina_signals": signals,
        "website_status": status,
        "pages_read": [p["url"] for p in pages],
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


#  Running over a merged file --

def select(startups: list[dict], include_all: bool, names: list[str]) -> list[dict]:
    if names:
        wanted = {n.casefold() for n in names}
        return [s for s in startups if s["name"].casefold() in wanted
                or any(a.casefold() in wanted for a in s.get("aliases", []))]
    if include_all:
        return startups
    return [s for s in startups if (s.get("country") or "Argentina") == "Argentina"]


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
    todo = [s for s in select(merged["startups"], args.all, args.names) if s["name"] not in done]
    if args.limit is not None:
        todo = todo[: args.limit]
    print(f"{len(done)} already enriched, {len(todo)} to do -> {out}")

    from openai import OpenAI
    from src.core.browser import BrowserManager

    classify = make_llm_classifier(OpenAI(base_url=config.VLLM_BASE_URL, api_key=config.API_KEY), config.VLLM_MODEL)
    result = {"enriched_at": None, "source": str(merged_path), "taxonomy": str(args.taxonomy), "startups": []}

    def save() -> None:
        startups = [done.get(s["name"], s) for s in merged["startups"]]
        result["enriched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["startups"] = startups
        result["summary"] = summarize(startups)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    with BrowserManager() as browser:
        for i, startup in enumerate(todo, 1):
            try:
                enrichment = enrich_one(startup, browser, classify, taxonomy)
            except Exception as exc:
                print(f"[{i}/{len(todo)}] {startup['name']}: FAILED {type(exc).__name__}: {exc}")
                continue
            done[startup["name"]] = {**startup, "enrichment": enrichment}
            save()
            print(f"[{i}/{len(todo)}] {startup['name']}: {enrichment.get('country') or '?'}, "
                  f"{'deep tech' if enrichment.get('deep_tech') else 'not deep tech'} "
                  f"({enrichment.get('tech_area')}, {enrichment.get('confidence')})"
                  + (f"  checks: {'; '.join(enrichment['checks'])}" if enrichment["checks"] else ""))
    save()
    print(json.dumps(result["summary"], indent=2))


def summarize(startups: list[dict]) -> dict[str, Any]:
    enriched = [s["enrichment"] for s in startups if s.get("enrichment")]
    argentine = [e for e in enriched if e.get("country") == "Argentina"]
    return {
        "enriched": len(enriched),
        "argentina": len(argentine),
        "argentina_deep_tech": sum(1 for e in argentine if e.get("deep_tech")),
        "deep_tech": sum(1 for e in enriched if e.get("deep_tech")),
        "by_area": dict(Counter(e.get("tech_area") for e in enriched).most_common()),
        "country_unknown": sum(1 for e in enriched if not e.get("country")),
    }


if __name__ == "__main__":
    main()
