"""Tests for src/db/enrichment.py against a real PostgreSQL (fixtures in conftest.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from src.db.enrichment import apply_enrichment  # noqa: E402
from src.db.loader import load_portfolio  # noqa: E402
from src.db.resolution import merge_actors  # noqa: E402

pytestmark = pytest.mark.db

ENRICHMENT = {"deep_tech": True, "tech_area": "biotech_agri_food", "summary": "Soil microbes.", "year_founded": 2019,
              "country": "Argentina"}


def _load(conn, startups):
    load_portfolio(conn, {"source_url": "https://fund.example/portfolio", "fund_name": "Fund", "startups": startups,
                          "errors": []}, fund_site="fund.example", fund_name="Fund", run_ref="t.json")


def _actor(conn, name):
    return conn.execute(
        "SELECT metadata -> 'enrichment', description, year_created FROM active_actors WHERE name = %s", (name,)
    ).fetchone()


def test_stored_by_website_and_fills_only_empty_fields(conn):
    _load(conn, [{"name": "Puna", "website": "https://puna.bio"},
                 {"name": "Nova", "website": "https://nova.com", "description": "From the fund."}])
    stats = apply_enrichment(conn, [
        {"name": "Puna Bio", "website": "https://www.puna.bio/es", "enrichment": ENRICHMENT},
        {"name": "Nova", "website": "https://nova.com", "enrichment": ENRICHMENT},
    ])
    assert (stats.startups_matched, stats.actors_updated, stats.not_found) == (2, 2, [])
    assert _actor(conn, "Puna") == (ENRICHMENT, "Soil microbes.", 2019)
    assert _actor(conn, "Nova")[1] == "From the fund."


def test_matched_by_alias_but_not_across_domains(conn):
    _load(conn, [{"name": "Acme"}, {"name": "Nova", "website": "https://nova.com"}])
    stats = apply_enrichment(conn, [
        {"name": "Acme Labs", "aliases": ["Acme"], "enrichment": ENRICHMENT},
        {"name": "Nova", "website": "https://nova.com.ar", "enrichment": ENRICHMENT},
        {"name": "Nobody", "enrichment": ENRICHMENT},
        {"name": "Not done"},
    ])
    assert _actor(conn, "Acme")[0] == ENRICHMENT
    assert _actor(conn, "Nova")[0] is None
    assert stats.not_found == ["Nova", "Nobody"] and stats.not_enriched == 1


def test_loading_again_replaces_the_result(conn):
    _load(conn, [{"name": "Puna", "website": "https://puna.bio"}])
    apply_enrichment(conn, [{"name": "Puna", "website": "https://puna.bio", "enrichment": ENRICHMENT}])
    apply_enrichment(conn, [{"name": "Puna", "website": "https://puna.bio", "enrichment": {"deep_tech": False}}])
    assert _actor(conn, "Puna")[0] == {"deep_tech": False}


def test_a_merge_keeps_the_enrichment(conn):
    _load(conn, [{"name": "Puna", "website": "https://puna.bio"}, {"name": "Puna Two", "website": "https://puna2.bio"}])
    apply_enrichment(conn, [{"name": "Puna Two", "website": "https://puna2.bio", "enrichment": ENRICHMENT}])
    keep, drop = (conn.execute("SELECT id FROM actors WHERE name = %s", (n,)).fetchone()[0] for n in ("Puna", "Puna Two"))
    merge_actors(conn, keep, drop)
    assert _actor(conn, "Puna")[0] == ENRICHMENT
