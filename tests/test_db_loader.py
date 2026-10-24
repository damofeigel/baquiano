"""Tests for src/db/loader.py against a real PostgreSQL (fixtures in conftest.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from src.db.loader import load_portfolio  # noqa: E402

pytestmark = pytest.mark.db


def _portfolio(source_url: str, startups: list[dict]) -> dict:
    return {"source_url": source_url, "fund_name": "x", "startups": startups, "errors": []}


GRIDX = _portfolio("https://www.gridexponential.com/portfolio", [
    {"name": "Beeflow", "website": "https://beeflow.com", "description": "Pollination technologies"},
    {"name": "Puna Bio", "website": "https://puna.bio", "country": "Argentina"},
    {"name": "CASPR", "website": None},
])


def _count(conn, query: str, *params) -> int:
    return conn.execute(query, params).fetchone()[0]


def test_first_load_creates_fund_startups_and_investments(conn):
    stats = load_portfolio(conn, GRIDX, fund_site="gridexponential.com", fund_name="GRIDX", run_ref="gridx.json")

    assert (stats.mentions_new, stats.new_actors, stats.relationships) == (4, 4, 3)
    names = {r[0] for r in conn.execute("SELECT name FROM active_actors").fetchall()}
    assert names == {"GRIDX", "Beeflow", "Puna Bio", "CASPR"}
    assert _count(conn, """SELECT count(*) FROM actor_type_assignments ta JOIN actor_types t ON t.id = ta.actor_type_id
                           WHERE t.name = 'Startup'""") == 3
    assert {r[0] for r in conn.execute("SELECT value FROM actor_identifiers").fetchall()} == {
        "gridexponential.com", "beeflow.com", "puna.bio"}
    assert _count(conn, """SELECT count(*) FROM relationships r JOIN relationship_types t ON t.id = r.relationship_type_id
                           JOIN actors f ON f.id = r.source_actor_id
                           WHERE t.name = 'Investment' AND f.name = 'GRIDX' AND r.method = 'portfolio'""") == 3
    assert _count(conn, "SELECT count(*) FROM sources WHERE url = %s", GRIDX["source_url"]) == 1


def test_loading_the_same_file_again_adds_nothing(conn):
    load_portfolio(conn, GRIDX, fund_site="gridexponential.com", fund_name="GRIDX", run_ref="gridx.json")
    before = [_count(conn, f"SELECT count(*) FROM {t}") for t in ("mentions", "actors", "relationships", "sources")]
    stats = load_portfolio(conn, GRIDX, fund_site="gridexponential.com", fund_name="GRIDX", run_ref="gridx-2.json")
    after = [_count(conn, f"SELECT count(*) FROM {t}") for t in ("mentions", "actors", "relationships", "sources")]

    assert before == after
    assert (stats.mentions_new, stats.mentions_existing) == (0, 4)


def test_same_startup_in_a_second_fund_is_linked_by_domain(conn):
    load_portfolio(conn, GRIDX, fund_site="gridexponential.com", fund_name="GRIDX", run_ref="gridx.json")
    yield_lab = _portfolio("https://theyieldlab.asia/", [
        {"name": "BEEFLOW CORP", "website": "https://www.beeflow.com/", "country": "Argentina"},
    ])
    stats = load_portfolio(conn, yield_lab, fund_site="theyieldlab.com", fund_name="The Yield Lab", run_ref="tyl.json")

    assert stats.linked == 1 and stats.new_actors == 1      # the startup linked; the new fund created
    beeflow = conn.execute("SELECT id FROM actors WHERE name = 'Beeflow'").fetchone()[0]
    aliases = {r[0] for r in conn.execute("SELECT alias FROM actor_aliases WHERE actor_id = %s", (beeflow,)).fetchall()}
    assert aliases == {"Beeflow", "BEEFLOW CORP"}
    funds = {r[0] for r in conn.execute(
        """SELECT f.name FROM relationships r JOIN actors f ON f.id = r.source_actor_id
           WHERE r.target_actor_id = %s""", (beeflow,)).fetchall()}
    assert funds == {"GRIDX", "The Yield Lab"}


def test_same_name_without_identifier_goes_to_review(conn):
    load_portfolio(conn, GRIDX, fund_site="gridexponential.com", fund_name="GRIDX", run_ref="gridx.json")
    other = _portfolio("https://newtopia.vc/portfolio/", [{"name": "Puna Bio S.A.", "website": None}])
    stats = load_portfolio(conn, other, fund_site="newtopia.vc", fund_name="Newtopia VC", run_ref="nt.json")

    assert stats.unsure == 1
    assert _count(conn, "SELECT count(*) FROM actors WHERE name_key = 'punabio'") == 1   # not duplicated
    queue = conn.execute("SELECT record_name, candidate_name FROM match_review_queue").fetchall()
    assert queue == [("Puna Bio S.A.", "Puna Bio")]
    # Its investment waits until the mention is resolved
    assert _count(conn, "SELECT count(*) FROM mention_relations WHERE relationship_id IS NULL") == 1


def test_different_companies_with_the_same_name_key_are_not_merged(conn):
    a = _portfolio("https://a.example/portfolio", [{"name": "Kigüi", "website": "https://kigui.mx"}])
    b = _portfolio("https://b.example/portfolio", [{"name": "Kigui", "website": "https://kigui.ai"}])
    load_portfolio(conn, a, fund_site="a.example", fund_name="Fund A", run_ref="a.json")
    stats = load_portfolio(conn, b, fund_site="b.example", fund_name="Fund B", run_ref="b.json")

    assert stats.unsure == 1
    assert _count(conn, "SELECT count(*) FROM actors WHERE name_key = 'kigui'") == 1
    [(features,)] = conn.execute("SELECT features FROM match_decisions").fetchall()
    assert features["mention_domain"] == "kigui.ai" and features["candidate_domains"] == ["kigui.mx"]


def test_shared_platform_links_never_merge_companies(conn):
    data = _portfolio("https://fund.example/portfolio", [
        {"name": "CASPR Biotech", "website": "https://www.linkedin.com/company/casprbio/"},
        {"name": "Synthetic Ocular Restoration", "website": "https://www.linkedin.com/company/sor/"},
    ])
    stats = load_portfolio(conn, data, fund_site="fund.example", fund_name="Fund", run_ref="f.json")

    assert stats.new_actors == 3                                    # fund + 2 startups
    assert _count(conn, "SELECT count(*) FROM actor_identifiers WHERE value = 'linkedin.com'") == 0
