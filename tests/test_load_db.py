"""Tests for scripts/load_db.py: files are cleaned like merge_portfolios.py cleans them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import load_db  # noqa: E402

from src.db.loader import load_portfolio  # noqa: E402


def _fund(path: Path, fund: str, site: str, startups: list[dict]) -> Path:
    path.write_text(json.dumps({"source_url": f"https://{site}/portfolio", "fund_name": fund,
                                "startups": startups, "errors": []}))
    return path


@pytest.fixture
def files(tmp_path):
    main = _fund(tmp_path / "theyieldlab.com_20261014-234417.json", "The Yield Lab", "theyieldlab.com", [
        {"name": "Beeflow", "website": "https://beeflow.com"},
        {"name": "Exited", "website": "http://www.agribodytech.com", "description": "Crop science startup"},
    ])
    latam = _fund(tmp_path / "theyieldlablatam.com_20261022-151120.json", "The Yield Lab Latam",
                  "theyieldlablatam.com", [
        {"name": "Verqor", "website": "https://verqor.com", "country": "MÉXICO"},
        {"name": "AI & SaaS", "website": "https://theyieldlablatam.com/ai-saas"},    # the fund's own site
        {"name": ""},
    ])
    return main, latam


def test_a_sister_site_is_loaded_as_its_main_fund(files):
    main, latam = files
    by_file = {path: (site, name) for path, _, site, name, _ in load_db.prepare([main, latam])}
    assert by_file[latam] == by_file[main] == ("theyieldlab.com", "The Yield Lab")


def test_a_sister_site_alone_keeps_its_own_identity(files):
    _, latam = files
    [(_, _, site, name, _)] = load_db.prepare([latam])
    assert (site, name) == ("theyieldlablatam.com", "The Yield Lab Latam")


def test_startups_are_cleaned_before_loading(files):
    main, latam = files
    prepared = {path: (data, notes) for path, data, _, _, notes in load_db.prepare([main, latam])}

    data, notes = prepared[main]
    assert [s["name"] for s in data["startups"]] == ["Beeflow", "Agribodytech"]
    assert data["startups"][1]["renamed_from"] == "Exited"
    assert notes == ["renamed 'Exited' to 'Agribodytech' (from http://www.agribodytech.com)"]

    data, notes = prepared[latam]
    assert [(s["name"], s["country"]) for s in data["startups"]] == [("Verqor", "Mexico")]
    assert data["source_url"] == "https://theyieldlablatam.com/portfolio"     # the rest of the file is kept
    assert len(notes) == 1 and notes[0].startswith("dropped 'AI & SaaS'")


@pytest.mark.db
def test_the_database_gets_one_fund_and_no_tags(conn, files):
    for path, data, site, name, _ in load_db.prepare(list(files)):
        load_portfolio(conn, data, fund_site=site, fund_name=name, run_ref=path.name)

    names = {r[0] for r in conn.execute("SELECT name FROM active_actors")}
    assert names == {"The Yield Lab", "Beeflow", "Agribodytech", "Verqor"}
    assert conn.execute("SELECT count(*) FROM match_review_queue").fetchone()[0] == 0
    investments = conn.execute(
        """SELECT count(*) FROM relationships r JOIN actors f ON f.id = r.source_actor_id
           WHERE f.name = 'The Yield Lab'""").fetchone()[0]
    assert investments == 3
