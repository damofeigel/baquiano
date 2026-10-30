"""Tests for scripts/enrich_startups.py (fake browser and model: no network, no vLLM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import enrich_startups as es  # noqa: E402
import pytest  # noqa: E402

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


def test_a_website_without_scheme_is_opened_with_https():
    browser = FakeBrowser({"https://puna.bio": PUNA_HOME, "https://puna.bio/about": PUNA_ABOUT})
    pages, status = es.collect_pages(browser, "puna.bio")
    assert status == "ok" and browser.visited == ["https://puna.bio", "https://puna.bio/about"]


def test_unreachable_site_is_reported_not_raised():
    pages, status = es.collect_pages(FakeBrowser({}), "https://gone.example")
    assert pages == [] and status == "unreachable: TimeoutError: https://gone.example"


def test_argentina_signals():
    assert es.argentina_signals("https://neworgans.com.ar", []) == [".ar domain"]
    assert es.argentina_signals("neworgans.com.ar", []) == [".ar domain"]          # no scheme
    assert es.argentina_signals("www.NEWORGANS.com.ar/es", []) == [".ar domain"]
    assert es.argentina_signals("neworgans.com/ar", []) == []                      # a path, not the domain
    assert es.argentina_signals("empresa.com.ar/?next=https://x.com", []) == [".ar domain"]
    assert es.argentina_signals("neworgans.com.ar ", []) == [".ar domain"]           # a space
    assert es.argentina_signals("neworgans.com.ar.", []) == [".ar domain"]           # a final dot
    assert es.argentina_signals("https://neworgans.com", [], "https://www.neworgans.com.ar/") == [".ar domain"]
    assert es.argentina_signals("https://neworgans.com", [], "https://neworgans.com/es") == []
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



def test_fund_labels_are_given_as_a_hint():
    startup = {"name": "Puna Bio", "sector": "Agtech", "conflicts": {"sector": ["Agtech", "Agri·food·land use"]},
               "descriptions": []}
    assert es.fund_labels(startup) == ["Agtech", "Agri·food·land use"]
    assert es.fund_labels({"name": "Stämm", "sector": "Biotech"}) == ["Biotech"]
    assert es.fund_labels({"name": "Antarka"}) == []

    user = es.build_messages(startup, [], [], TAXONOMY)[1]["content"]
    assert "Category labels funds give it (their own wording, a hint only): Agtech, Agri·food·land use" in user

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


# --- Checking the LLM server before starting -----------------------------------------

class FakeModels:
    def __init__(self, ids=None, error=None):
        self.ids, self.error = ids or [], error

    def list(self):
        if self.error:
            raise self.error
        return [SimpleNamespace(id=i) for i in self.ids]


def test_check_llm_reports_an_unreachable_server():
    client = SimpleNamespace(models=FakeModels(error=ConnectionError("Connection refused")))
    problem = es.check_llm(client, "openai/gpt-oss-20b")
    assert "Can't reach the LLM server" in problem and "Is vLLM running?" in problem


def test_check_llm_reports_a_model_the_server_does_not_serve():
    client = SimpleNamespace(models=FakeModels(ids=["openai/gpt-oss-20b"]))
    assert "It serves: openai/gpt-oss-20b" in es.check_llm(client, "meta-llama/Llama-3.1-8B-Instruct")
    assert es.check_llm(client, "openai/gpt-oss-20b") is None


# --- Hints, reuse and error detail -------------------------------------------------------------

@pytest.mark.parametrize("enrichment, country, likely", [
    ({"country": None, "argentina_signals": [".ar domain"]}, None, "Argentina"),
    ({"country": None, "argentina_signals": ["phone +54", "Argentine city"]}, None, "Argentina"),
    ({"country": None, "argentina_signals": ["mentions Argentina", "Argentine city"]}, None, None),  # weak
    ({"country": "USA", "argentina_signals": [".ar domain"]}, "United States", "United States"),     # stated wins
    ({"country": "Argentina", "argentina_signals": []}, "Argentina", "Argentina"),
    ({"country": None}, None, None),
])
def test_country_hints(enrichment, country, likely):
    hinted = es.add_hints(dict(enrichment))
    assert (hinted["country"], hinted["country_likely"]) == (country, likely)


def test_likely_argentine_startups_are_counted_apart():
    startups = [{"enrichment": {"country": "Argentina", "deep_tech": True}},
                {"enrichment": {"country": None, "country_likely": "Argentina", "deep_tech": True}},
                {"enrichment": {"country": None, "country_likely": None, "deep_tech": False}}]
    summary = es.summarize(startups)
    assert (summary["argentina"], summary["argentina_likely"], summary["argentina_likely_deep_tech"]) == (1, 1, 1)


def test_earlier_results_are_reused_by_name_or_website(tmp_path):
    old = tmp_path / "startups_enriched_old.json"
    old.write_text(json.dumps({"startups": [
        {"name": "Exited", "website": "http://www.agribodytech.com", "enrichment": {"tech_area": "biotech_agri_food"}},
        {"name": "Kilimo", "aliases": ["KILIMO"], "enrichment": {"tech_area": "agtech_digital"}},
        {"name": "Never enriched"},
    ]}))
    startups = [{"name": "Agribodytech", "website": "https://agribodytech.com/"},   # renamed since
                {"name": "kilimo", "aliases": ["kilimo"]},
                {"name": "Never enriched"},
                {"name": "Puna Bio"}]
    done = {"Puna Bio": {"name": "Puna Bio", "enrichment": {"tech_area": "x"}}}

    assert es.reuse_previous(startups, [old], done) == 2
    assert done["Agribodytech"]["enrichment"]["tech_area"] == "biotech_agri_food"
    assert done["kilimo"]["enrichment"]["tech_area"] == "agtech_digital"
    assert "Never enriched" not in done and done["Puna Bio"]["enrichment"]["tech_area"] == "x"


def test_unreachable_site_keeps_the_error_message():
    class Failing:
        def go_to(self, url):
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED at https://calfix.bio/\nCall log: ...")
    pages, status = es.collect_pages(Failing(), "https://calfix.bio/")
    assert pages == [] and status == "unreachable: RuntimeError: net::ERR_NAME_NOT_RESOLVED at https://calfix.bio/"



def _previous(tmp_path, startups):
    path = tmp_path / "startups_enriched_old.json"
    path.write_text(json.dumps({"startups": startups}))
    return path


def test_a_shared_platform_link_is_not_an_identity(tmp_path):
    old = _previous(tmp_path, [{"name": "Acme", "website": "https://www.linkedin.com/company/acme",
                                "enrichment": {"tech_area": "fintech"}}])
    startups = [{"name": "Brava", "website": "https://www.linkedin.com/company/brava"},
                {"name": "Cora", "website": "https://linkedin.com/company/cora"}]
    done: dict = {}
    assert es.reuse_previous(startups, [old], done) == 0 and done == {}


@pytest.mark.parametrize("old_site, new_site, reused", [
    ("https://nova.com", "https://nova.com.ar", False),         # same name, two different companies
    ("https://nova.com", None, True),                           # one side has no website: the name decides
    (None, "https://nova.com.ar", True),
    ("https://www.linkedin.com/company/nova", "https://nova.com.ar", True),   # a platform link isn't a domain
    ("https://nova.com", "https://www.nova.com/en", True),      # same domain
])
def test_same_name_is_reused_only_when_the_websites_agree(tmp_path, old_site, new_site, reused):
    old = _previous(tmp_path, [{"name": "Nova", "website": old_site, "enrichment": {"country": "United States"}}])
    done: dict = {}
    assert es.reuse_previous([{"name": "Nova", "website": new_site}], [old], done) == int(reused)


def test_each_reused_startup_gets_its_own_copy(tmp_path):
    old = _previous(tmp_path, [{"name": "Kilimo", "aliases": ["KILIMO"], "website": "https://kilimo.com",
                                "enrichment": {"country": None, "argentina_signals": [".ar domain"]}}])
    startups = [{"name": "Kilimo", "website": "https://kilimo.com"}, {"name": "KILIMO"}]
    done: dict = {}
    assert es.reuse_previous(startups, [old], done) == 2
    done["Kilimo"]["enrichment"]["country"] = "Argentina"
    assert done["KILIMO"]["enrichment"]["country"] is None



def test_selection_reads_any_spelling_of_argentina():
    """Merged files written before countries were normalized."""
    startups = [{"name": "A", "country": "ARGENTINA"}, {"name": "B", "country": "República Argentina"},
                {"name": "C", "country": None}, {"name": "D", "country": "brasil"}]
    assert [s["name"] for s in es.select(startups, False, [])] == ["A", "B", "C"]


def test_old_results_get_hints_once_when_loaded(tmp_path, monkeypatch):
    merged = tmp_path / "startups_merged_x.json"
    merged.write_text(json.dumps({"startups": [
        {"name": "Acme", "country": "Argentina"}, {"name": "Nova", "country": "Argentina"}, {"name": "Todo"}]}))
    out = tmp_path / "startups_enriched_x.json"           # a run to resume, from before hints existed
    out.write_text(json.dumps({"startups": [
        {"name": "Acme", "enrichment": {"country": "USA", "argentina_signals": []}}]}))
    old = _previous(tmp_path, [{"name": "Nova", "enrichment": {"country": None, "argentina_signals": ["phone +54"]}}])
    calls = []
    real = es.add_hints
    monkeypatch.setattr(es, "add_hints", lambda e: calls.append(1) or real(e))
    monkeypatch.setattr(sys, "argv", ["enrich_startups.py", str(merged), "--out", str(out),
                                      "--previous", str(old), "--limit", "0"])
    es.main()

    saved = {s["name"]: s.get("enrichment") for s in json.loads(out.read_text())["startups"]}
    assert (saved["Acme"]["country"], saved["Acme"]["country_likely"]) == ("United States", "United States")
    assert (saved["Nova"]["country"], saved["Nova"]["country_likely"]) == (None, "Argentina")
    assert saved["Todo"] is None
    assert len(calls) == 2                                 # once per loaded result, not on every save


def test_startups_matching_one_old_record_get_their_own_copy(tmp_path):
    old = _previous(tmp_path, [{"name": "Kilimo", "aliases": ["KILIMO"], "website": "https://kilimo.com",
                                "enrichment": {"country": None, "argentina_signals": [".ar domain"],
                                               "checks": ["year not on the pages"]}}])
    startups = [{"name": "Kilimo", "website": "https://kilimo.com"}, {"name": "KILIMO"}]   # by domain, by alias
    done: dict = {}
    assert es.reuse_previous(startups, [old], done) == 2

    first, second = done["Kilimo"]["enrichment"], done["KILIMO"]["enrichment"]
    first["country"] = "Argentina"
    first["checks"].append("changed")
    assert second["country"] is None and second["checks"] == ["year not on the pages"]


@pytest.mark.parametrize("website, stored, pages_read, signals", [
    ("puna.com.ar", [], [], [".ar domain"]),                              # stored before the scheme fix
    ("https://puna.com.ar", ["phone +54"], [], [".ar domain", "phone +54"]),
    ("https://puna.bio", [".ar domain", "Argentine city"], [], ["Argentine city"]),   # the website changed since
    ("https://puna.bio", [], ["https://puna.com.ar/", "https://puna.bio/about"], [".ar domain"]),   # redirected
    ("https://puna.bio", [], ["https://puna.bio/", "https://puna.com.ar/about"], []),  # only the landing page
    (None, None, None, []),
])
def test_the_domain_signal_is_recomputed_from_the_website(website, stored, pages_read, signals):
    entry = {"name": "Puna", "website": website, "enrichment": {"argentina_signals": stored, "pages_read": pages_read}}
    assert es.refresh_domain_signal(entry)["enrichment"]["argentina_signals"] == signals


def test_old_results_get_the_domain_signal_on_load(tmp_path, monkeypatch):
    merged = tmp_path / "startups_merged_x.json"
    merged.write_text(json.dumps({"startups": [{"name": "Puna", "website": "puna.com.ar"},
                                               {"name": "Nova", "website": "www.nova.com.ar"}]}))
    out = tmp_path / "startups_enriched_x.json"                         # resumed
    out.write_text(json.dumps({"startups": [{"name": "Puna", "website": "puna.com.ar", "enrichment": {
        "country": None, "argentina_signals": [], "deep_tech": True}}]}))
    old = _previous(tmp_path, [{"name": "Nova", "website": "https://nova.com.ar", "enrichment": {
        "country": None, "argentina_signals": []}}])                     # from an earlier file
    monkeypatch.setattr(sys, "argv", ["enrich_startups.py", str(merged), "--out", str(out),
                                      "--previous", str(old), "--limit", "0"])
    es.main()

    result = json.loads(out.read_text())
    saved = {s["name"]: s["enrichment"] for s in result["startups"]}
    assert all(e["argentina_signals"] == [".ar domain"] and e["country_likely"] == "Argentina"
               for e in saved.values())
    assert (result["summary"]["argentina_likely"], result["summary"]["argentina_likely_deep_tech"]) == (2, 1)


def test_a_redirect_to_an_ar_site_counts(monkeypatch):
    home = SimpleNamespace(url="https://www.neworgans.com.ar/", plain_text="Organ printing", links=[])
    monkeypatch.setattr(es, "verify", lambda result, texts, taxonomy: {"country": None, "checks": []})
    enrichment = es.enrich_one({"name": "New Organs", "website": "https://neworgans.com"},
                               FakeBrowser({"https://neworgans.com": home}), lambda messages, schema: {}, TAXONOMY)
    assert enrichment["argentina_signals"] == [".ar domain"] and enrichment["country_likely"] == "Argentina"
    assert enrichment["pages_read"] == ["https://www.neworgans.com.ar/"]
