"""Tests for src/db/resolution.py against a real PostgreSQL (fixtures in conftest.py)."""

from __future__ import annotations

import csv

import pytest

pytest.importorskip("psycopg")

from src.db.loader import load_portfolio  # noqa: E402
from src.db.resolution import apply_review, export_review, resolve_all, trigram_similarity  # noqa: E402

pytestmark = pytest.mark.db


def _load(conn, site: str, fund: str, startups: list[dict]) -> None:
    data = {"source_url": f"https://{site}/portfolio", "fund_name": fund, "startups": startups, "errors": []}
    load_portfolio(conn, data, fund_site=site, fund_name=fund, run_ref=f"{site}.json")


def _one(conn, query: str, *params):
    return conn.execute(query, params).fetchone()[0]


def _active(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM active_actors").fetchall()}


def _review(conn, tmp_path, decisions: dict[str, str]) -> dict[str, int]:
    """Export the queue, answer by right-hand name, apply."""
    path = tmp_path / "review.csv"
    export_review(conn, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    for row in rows:
        row["your_decision"] = decisions.get(row["right_name"], "")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return apply_review(conn, path, "tester")


def test_duplicate_actors_are_merged_and_keep_their_history(conn):
    _load(conn, "a.example", "Fund A", [{"name": "Heartbest", "country": "Mexico",
                                         "description": "Plant based dairy alternatives made with fermentation"}])
    _load(conn, "b.example", "Fund B", [{"name": "Heartbest Foods", "country": "Mexico",
                                         "description": "Dairy alternatives from plant based fermentation"}])
    assert {"Heartbest", "Heartbest Foods"} <= _active(conn)        # the loader can't tell

    stats = resolve_all(conn)

    assert stats.merged == 1
    assert "Heartbest Foods" not in _active(conn) and "Heartbest" in _active(conn)
    keep = _one(conn, "SELECT id FROM actors WHERE name = 'Heartbest'")
    assert _one(conn, "SELECT merged_into_id FROM actors WHERE name = 'Heartbest Foods'") == keep
    assert _one(conn, "SELECT count(*) FROM mentions WHERE actor_id = %s", keep) == 2
    funds = {r[0] for r in conn.execute(
        """SELECT f.name FROM relationships r JOIN actors f ON f.id = r.source_actor_id
           WHERE r.target_actor_id = %s""", (keep,)).fetchall()}
    assert funds == {"Fund A", "Fund B"}
    aliases = {r[0] for r in conn.execute("SELECT alias FROM actor_aliases WHERE actor_id = %s", (keep,)).fetchall()}
    assert aliases == {"Heartbest", "Heartbest Foods"}


def test_running_again_changes_nothing(conn):
    _load(conn, "a.example", "Fund A", [{"name": "Kilimo", "country": "Argentina"}, {"name": "Glaia"}])
    _load(conn, "b.example", "Fund B", [{"name": "Kilimo Labs"}, {"name": "Glaia 2"}])
    resolve_all(conn)
    before = [_one(conn, f"SELECT count(*) FROM {t}") for t in ("match_decisions", "actors", "relationships")]

    stats = resolve_all(conn)

    assert [_one(conn, f"SELECT count(*) FROM {t}") for t in ("match_decisions", "actors", "relationships")] == before
    assert stats.merged == 0


def test_unresolved_mention_is_linked_when_the_evidence_agrees(conn):
    _load(conn, "gridexponential.com", "GRIDX", [{"name": "Puna Bio", "website": "https://puna.bio",
        "country": "Argentina", "description": "Biological inputs for agriculture using extremophiles"}])
    _load(conn, "theyieldlab.com", "The Yield Lab", [{"name": "PUNA BIO CORP", "country": "Argentina",
        "description": "Extremophile microbes as biological inputs for agriculture"}])
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 1           # left by the loader

    stats = resolve_all(conn)

    assert (stats.linked, stats.relationships) == (1, 1)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0
    assert _one(conn, "SELECT count(*) FROM active_actors WHERE name_key = 'punabio'") == 1
    assert _one(conn, "SELECT count(*) FROM mention_relations WHERE relationship_id IS NULL") == 0


def test_companies_listed_side_by_side_stay_apart(conn):
    _load(conn, "a.example", "Fund A", [{"name": "Startup 1"}, {"name": "Startup 10"},
                                        {"name": "Eywa Biotech"}, {"name": "ZEV Biotech"}])
    stats = resolve_all(conn)

    assert stats.merged == 0
    assert {"Startup 1", "Startup 10", "Eywa Biotech", "ZEV Biotech"} <= _active(conn)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0


def test_human_no_match_creates_the_actor_and_is_not_asked_again(conn, tmp_path):
    _load(conn, "theyieldlab.com", "The Yield Lab", [{"name": "Kigüi", "country": "Mexico",
        "description": "Food & Ag Logistics & Storage Solutions"}])
    _load(conn, "newtopia.vc", "Newtopia VC", [{"name": "Kigui",
        "description": "AI agents that run retail execution, detecting out-of-stocks"}])
    resolve_all(conn)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 1

    counts = _review(conn, tmp_path, {"Kigüi": "no_match"})

    assert counts["new actor"] == 1 and counts["relationships"] == 1
    assert _one(conn, "SELECT count(*) FROM active_actors WHERE name_key = 'kigui'") == 2
    resolve_all(conn)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0           # the answer holds


def test_human_match_merges_and_a_later_run_respects_it(conn, tmp_path):
    _load(conn, "a.example", "Fund A", [{"name": "Kilimo", "country": "Argentina"}])
    _load(conn, "b.example", "Fund B", [{"name": "Kilimo Labs"}])
    resolve_all(conn)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 1

    counts = _review(conn, tmp_path, {"Kilimo Labs": "match", "Kilimo": "match"})

    assert counts["merged"] == 1
    assert len({"Kilimo", "Kilimo Labs"} & _active(conn)) == 1
    resolve_all(conn)
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0
    assert _one(conn, "SELECT count(*) FROM match_decisions WHERE is_current AND method = 'human'") == 1


@pytest.mark.parametrize("a, b", [("agree", "agreena"), ("heartbest", "heartbest foods"), ("kigui", "kigui ai")])
def test_python_trigrams_match_postgres(conn, a, b):
    assert trigram_similarity(a, b) == pytest.approx(_one(conn, "SELECT similarity(%s, %s)", a, b), abs=1e-6)
