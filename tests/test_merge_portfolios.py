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
