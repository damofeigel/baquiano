"""Tests for scripts/enrich_startups.py (fake browser and model: no network, no vLLM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import enrich_startups as es  # noqa: E402

TAXONOMY = json.loads(es.TAXONOMY_FILE.read_text())

PUNA_HOME = SimpleNamespace(
    url="https://puna.bio/",
    plain_text="# Puna Bio\nExtremophile bacteria for crops.\n[About us](https://puna.bio/about)",
    links=[{"text": "Blog", "href": "https://puna.bio/blog"},
           {"text": "About us", "href": "https://puna.bio/about"},
           {"text": "LinkedIn", "href": "https://linkedin.com/company/puna"}],
)
PUNA_ABOUT = SimpleNamespace(
    url="https://puna.bio/about",
    plain_text="Founded in 2019 by Argentine scientists. Offices: Av. Corrientes 1234, Buenos Aires, Argentina. +54 11 4444-5555",
    links=[],
)


class FakeBrowser:
    def __init__(self, pages):
        self.pages, self.visited = pages, []

    def go_to(self, url):
        self.visited.append(url)
        if url not in self.pages:
            raise TimeoutError(url)
        return self.pages[url]


def test_reads_homepage_and_about_page():
    browser = FakeBrowser({"https://puna.bio": PUNA_HOME, "https://puna.bio/about": PUNA_ABOUT})
    pages, status = es.collect_pages(browser, "https://puna.bio")
    assert status == "ok"
    assert [p["url"] for p in pages] == ["https://puna.bio/", "https://puna.bio/about"]
    assert "https://linkedin.com/company/puna" not in browser.visited   # other sites aren't followed


def test_unreachable_site_is_reported_not_raised():
    pages, status = es.collect_pages(FakeBrowser({}), "https://gone.example")
    assert pages == [] and status == "unreachable: TimeoutError"


def test_argentina_signals():
    assert es.argentina_signals("https://neworgans.com.ar", []) == [".ar domain"]
    signals = es.argentina_signals("https://puna.bio", [PUNA_ABOUT.plain_text])
    assert signals == ["phone +54", "mentions Argentina", "Argentine city"]
    assert es.argentina_signals("https://acme.com", ["Offices in Mexico City"]) == []


def _answer(**overrides):
    answer = {"country": "Argentina", "country_evidence": "Buenos Aires, Argentina",
              "year_founded": 2019, "year_evidence": "Founded in 2019", "summary": "Biologicals.",
              "deep_tech": True, "deep_tech_reason": "Microbiology R&D.", "tech_area": "biotech_agri_food",
              "confidence": "high"}
    return {**answer, **overrides}


def test_answers_backed_by_the_page_are_kept():
    result = es.verify(_answer(country_evidence="buenos  aires,   ARGENTINA"), [PUNA_ABOUT.plain_text], TAXONOMY)
    assert result["country"] == "Argentina" and result["year_founded"] == 2019
    assert result["checks"] == []


def test_invented_country_and_year_are_dropped():
    result = es.verify(
        _answer(country_evidence="Headquartered in Rosario", year_founded=2015, year_evidence="since 2015"),
        [PUNA_HOME.plain_text], TAXONOMY,
    )
    assert result["country"] is None and result["year_founded"] is None
    assert len(result["checks"]) == 2


def test_deep_tech_answer_that_contradicts_the_area_is_flagged():
    result = es.verify(_answer(deep_tech=True, tech_area="fintech"), [PUNA_ABOUT.plain_text], TAXONOMY)
    assert any("fintech is not deep tech" in c for c in result["checks"])


def test_schema_only_allows_taxonomy_areas():
    schema = es.result_schema(TAXONOMY)
    assert set(schema["properties"]["tech_area"]["enum"]) == set(TAXONOMY["areas"])
    assert set(schema["required"]) == set(schema["properties"])


def test_enrich_one_end_to_end():
    browser = FakeBrowser({"https://puna.bio": PUNA_HOME, "https://puna.bio/about": PUNA_ABOUT})
    seen = {}

    def fake_model(messages, schema):
        seen["user"] = messages[1]["content"]
        return _answer()

    startup = {"name": "Puna Bio", "website": "https://puna.bio",
               "descriptions": [{"fund": "GRIDX", "text": "Extremophile bacteria for agriculture"}]}
    enrichment = es.enrich_one(startup, browser, fake_model, TAXONOMY)

    assert enrichment["country"] == "Argentina" and enrichment["deep_tech"] is True
    assert enrichment["argentina_signals"] == ["phone +54", "mentions Argentina", "Argentine city"]
    assert enrichment["pages_read"] == ["https://puna.bio/", "https://puna.bio/about"]
    assert "GRIDX: Extremophile bacteria" in seen["user"]       # fund descriptions are given too


def test_no_website_uses_fund_descriptions_only():
    startup = {"name": "Werken Vac", "website": None,
               "descriptions": [{"fund": "GRIDX", "text": "Self-amplifying RNA vaccine for fish"}]}
    enrichment = es.enrich_one(startup, FakeBrowser({}), lambda m, s: _answer(country=None, country_evidence=None,
                               year_founded=None, year_evidence=None), TAXONOMY)
    assert enrichment["website_status"] == "no website" and enrichment["pages_read"] == []


def test_default_selection_is_argentina_or_unknown():
    startups = [{"name": "A", "country": "Argentina"}, {"name": "B", "country": None},
                {"name": "C", "country": "Brazil"}, {"name": "D", "country": "Mexico", "aliases": ["Dee"]}]
    assert [s["name"] for s in es.select(startups, False, [])] == ["A", "B"]
    assert len(es.select(startups, True, [])) == 4
    assert [s["name"] for s in es.select(startups, False, ["dee"])] == ["D"]


def test_parse_json_tolerates_text_around_it():
    assert es.parse_json('Here you go:\n```json\n{"country": null}\n```') == {"country": None}
