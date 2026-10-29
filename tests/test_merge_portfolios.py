"""Tests for scripts/merge_portfolios.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge_portfolios as mp  # noqa: E402


def _fund(path: Path, fund: str, startups: list[dict]) -> Path:
    path.write_text(json.dumps({"source_url": f"https://{fund}.example", "fund_name": fund,
                                "startups": startups, "errors": []}))
    return path


def test_same_startup_in_two_funds_is_merged(tmp_path):
    a = _fund(tmp_path / "gridx_20261013-154957.json", "GRIDX", [
        {"name": "Beeflow", "website": "https://beeflow.com", "description": "Pollination technologies"},
        {"name": "Puna Bio", "website": "https://puna.bio"},
    ])
    b = _fund(tmp_path / "yieldlab_20261022-104431.json", "The Yield Lab", [
        {"name": "Beeflow", "website": "https://www.beeflow.com/", "country": "Argentina",
         "description": "Nature based solutions to improve the impact of pollination in agriculture"},
        {"name": "Heartbest Foods", "website": None, "country": "Mexico"},
        {"name": "Heartbest", "website": "https://heartbest.com.mx"},
    ])
    merged = {s["name"]: s for s in mp.merge([a, b])}

    assert set(merged) == {"Beeflow", "Puna Bio", "Heartbest Foods"}
    beeflow = merged["Beeflow"]
    assert beeflow["funds"] == ["GRIDX", "The Yield Lab"]
    assert beeflow["country"] == "Argentina"                       # filled from the second fund
    assert beeflow["description"].startswith("Nature based")      # longest description wins
    assert len(beeflow["descriptions"]) == 2
    assert merged["Heartbest Foods"]["website"] == "https://heartbest.com.mx"


def test_disagreements_are_kept_as_conflicts(tmp_path):
    a = _fund(tmp_path / "a_20261001-000000.json", "Fund A", [{"name": "Acme", "country": "Argentina"}])
    b = _fund(tmp_path / "b_20261001-000000.json", "Fund B", [{"name": "ACME", "country": "Uruguay"}])
    [acme] = mp.merge([a, b])
    assert acme["country"] == "Argentina"
    assert acme["conflicts"] == {"country": ["Argentina", "Uruguay"]}
    assert acme["aliases"] == ["Acme", "ACME"]


def test_different_companies_stay_apart(tmp_path):
    a = _fund(tmp_path / "a_20261001-000000.json", "Fund A", [
        {"name": "Kigüi", "description": "Food logistics"}, {"name": "Startup 1"}, {"name": "Agree"},
    ])
    b = _fund(tmp_path / "b_20261001-000000.json", "Fund B", [
        {"name": "Kigui", "description": "Retail AI agents"}, {"name": "Startup 10"}, {"name": "Agreena"},
    ])
    assert len(mp.merge([a, b])) == 6


def test_latest_file_per_fund_and_non_portfolios_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "OUTPUT_DIR", tmp_path)
    _fund(tmp_path / "gridexponential.com_20261010-025932.json", "GRIDX", [{"name": "Old"}])
    newest = _fund(tmp_path / "gridexponential.com_20261013-154957.json", "GRIDX", [{"name": "New"}])
    other = _fund(tmp_path / "newtopia.vc_20261022-104437.json", "Newtopia VC", [{"name": "Aviva"}])
    (tmp_path / "lens_patents_gridexponential.com_AR_20261013.json").write_text(json.dumps({"records": []}))
    (tmp_path / "lens_counts_gridexponential.com_20261013.json").write_text(json.dumps({"startups": {}}))

    assert mp.find_inputs([], include_all=False) == sorted([newest, other])
    assert len(mp.find_inputs([], include_all=True)) == 3


def test_same_fund_named_differently_across_runs_is_one_fund(tmp_path):
    """--all over GridX runs listed startups as in "Grid Exponential, GRIDX"."""
    old = _fund(tmp_path / "gridexponential.com_20261010-174313.json", "Grid Exponential",
                [{"name": "Beeflow", "website": "https://beeflow.com"}])
    new = _fund(tmp_path / "gridexponential.com_20261013-154957.json", "GRIDX",
                [{"name": "Beeflow", "website": "https://beeflow.com"}])
    unnamed = _fund(tmp_path / "newtopia.vc_20261014-183757.json", "Unknown", [{"name": "Aviva"}])
    merged = {s["name"]: s for s in mp.merge([old, new, unnamed])}
    assert merged["Beeflow"]["funds"] == ["GRIDX"]          # newest run's name
    assert merged["Aviva"]["funds"] == ["Unknown"]


# --- Cleaning on the way in ------------------------------------------------------------------

def test_category_tags_read_as_names_are_renamed_or_dropped(tmp_path):
    """Replays 17 Sigma (logo cards with a tag) and The Yield Lab's "Exited" badge."""
    sigma = _fund(tmp_path / "17sigma.vc_20261022-134710.json", "17 Sigma", [
        {"name": "AI & SaaS", "sector": "AI & SaaS", "description": "AI & SaaS"},
        {"name": "Exit FinTech", "sector": "FinTech", "description": "FinTech"},
        {"name": "Web3", "sector": "Web3"},
        {"name": "Energy", "website": "https://www.lioraenergia.com.br/", "sector": "Energy", "description": "Energy"},
    ])
    yield_lab = _fund(tmp_path / "theyieldlab.com_20261014-234417.json", "The Yield Lab", [
        {"name": "Exited", "website": "http://www.agribodytech.com", "description": "Crop science startup"},
        {"name": "Kilimo", "website": "https://kilimo.com"},
    ])
    notes: list[str] = []
    merged = {s["name"]: s for s in mp.merge([sigma, yield_lab], notes)}

    assert set(merged) == {"Lioraenergia", "Agribodytech", "Kilimo"}
    assert merged["Lioraenergia"]["description"] is None                 # the tag repeated as description
    assert merged["Lioraenergia"]["renamed_from"] == ["Energy"]
    assert merged["Agribodytech"]["description"] == "Crop science startup"
    assert "Exited" not in merged["Agribodytech"]["aliases"]             # a badge must never match others
    assert len(notes) == 5
    assert "17 Sigma: dropped 'AI & SaaS': a category, not a company name, and no website" in notes


def test_names_of_ordinary_words_are_never_dropped(tmp_path):
    fund = _fund(tmp_path / "17sigma.vc_20261022-134710.json", "17 Sigma", [
        {"name": "Mobility", "description": "Electric scooters for Rosario"},          # no website
        {"name": "Crypto Energy", "website": "https://www.linkedin.com/company/ce"},   # only a platform
        {"name": "Logistics", "website": "https://logistics.io"},                     # the site confirms it
        {"name": "Marketplace", "website": "https://www.mercadoagro.com.ar"},         # renamed from the site
        {"name": "FinTech", "description": "FinTech"},                                # a tag word: dropped
    ])
    notes: list[str] = []
    merged = {s["name"]: s for s in mp.merge([fund], notes)}

    assert set(merged) == {"Mobility", "Crypto Energy", "Logistics", "Mercadoagro"}
    assert merged["Mobility"]["description"] == "Electric scooters for Rosario"
    assert "renamed_from" not in merged["Logistics"]
    assert merged["Mercadoagro"]["renamed_from"] == ["Marketplace"]
    assert notes == [
        "17 Sigma: kept 'Mobility': may be a sector tag or a real name, and no website to tell",
        "17 Sigma: kept 'Crypto Energy': may be a sector tag or a real name, and its only link is the "
        "fund's own site or a shared platform to tell",
        "17 Sigma: renamed 'Marketplace' to 'Mercadoagro' (from https://www.mercadoagro.com.ar)",
        "17 Sigma: dropped 'FinTech': a category, not a company name, and no website",
    ]


def test_a_regional_sister_site_is_the_same_fund(tmp_path):
    main = _fund(tmp_path / "theyieldlab.com_20261014-234417.json", "The Yield Lab", [
        {"name": "Kigüi", "website": None}, {"name": "Beeflow", "website": "https://beeflow.com"}])
    latam = _fund(tmp_path / "theyieldlablatam.com_20261022-151120.json", "The Yield Lab Latam", [
        {"name": "Kigüi", "website": None}, {"name": "Verqor"}])
    gridx = _fund(tmp_path / "gridexponential.com_20261022-123056.json", "GRIDX", [
        {"name": "Beeflow", "website": "https://beeflow.com"}])

    assert set(mp.fund_names([main, latam, gridx]).values()) == {"The Yield Lab", "GRIDX"}
    merged = {s["name"]: s for s in mp.merge([main, latam, gridx])}
    assert merged["Kigüi"]["funds"] == ["The Yield Lab"]                  # not "in two funds"
    assert merged["Verqor"]["funds"] == ["The Yield Lab"]
    assert merged["Beeflow"]["funds"] == ["The Yield Lab", "GRIDX"]


def test_countries_get_one_spelling(tmp_path):
    a = _fund(tmp_path / "a_20261001-000000.json", "Fund A", [
        {"name": "Acme", "country": "USA"}, {"name": "Brava", "country": "Brasil"}])
    b = _fund(tmp_path / "b_20261001-000000.json", "Fund B", [{"name": "ACME", "country": "United States"}])
    merged = {s["name"]: s for s in mp.merge([a, b])}

    assert merged["Acme"]["country"] == "United States" and merged["Acme"]["conflicts"] == {}
    assert merged["Brava"]["country"] == "Brazil"


def test_a_real_name_from_another_fund_replaces_one_made_from_the_website(tmp_path):
    sigma = _fund(tmp_path / "17sigma.vc_20261022-134710.json", "17 Sigma", [
        {"name": "Energy", "website": "https://www.lioraenergia.com.br/", "sector": "Energy"}])
    other = _fund(tmp_path / "kaszek.com_20261022-141546.json", "Kaszek", [
        {"name": "Liora Energia", "website": "https://lioraenergia.com.br", "description": "Solar for farms"}])
    again = _fund(tmp_path / "nxtp.vc_20261022-125406.json", "NXTP", [
        {"name": "Energy", "website": "https://lioraenergia.com.br", "sector": "Energy"}])

    [liora] = mp.merge([sigma, other, again])

    assert liora["name"] == "Liora Energia"                     # though 17 Sigma created the entry
    assert liora["aliases"] == ["Liora Energia"]                # the made-up name is not an alias
    assert liora["renamed_from"] == ["Energy"]                  # once, though two funds had the tag
    assert liora["funds"] == ["17 Sigma", "Kaszek", "NXTP"]
    assert "_name_from_website" not in liora


def test_a_name_made_from_the_website_stays_when_no_fund_gives_a_real_one(tmp_path):
    sigma = _fund(tmp_path / "17sigma.vc_20261022-134710.json", "17 Sigma", [
        {"name": "Energy", "website": "https://www.lioraenergia.com.br/", "sector": "Energy"}])
    [liora] = mp.merge([sigma])
    assert (liora["name"], liora["aliases"]) == ("Lioraenergia", [])



def test_tags_linking_to_the_funds_own_site_do_not_become_one_fake_startup(tmp_path):
    sigma = tmp_path / "17sigma.vc_20261022-134710.json"
    sigma.write_text(json.dumps({"source_url": "https://17sigma.com/portfolio", "fund_name": "17 Sigma", "errors": [],
        "startups": [
            {"name": "AI & SaaS", "website": "https://17sigma.vc/portfolio/ai-saas", "description": "AI & SaaS"},
            {"name": "FinTech", "website": "https://www.17sigma.com/fintech", "description": "FinTech"},
            {"name": "Energy", "website": "https://app.lioraenergia.com.br/", "sector": "Energy"},
        ]}))
    notes: list[str] = []
    merged = mp.merge([sigma], notes)

    assert [s["name"] for s in merged] == ["Lioraenergia"]       # not "17sigma", not "App"
    assert sum(note.startswith("17 Sigma: dropped") for note in notes) == 2
    assert all("the fund's own site" in note for note in notes if "dropped" in note)
