"""The LLM judge (src/db/judge.py) against a real PostgreSQL, with a scripted model (fixtures in conftest.py)."""

from __future__ import annotations

import csv

import pytest

pytest.importorskip("psycopg")

from src.db.judge import judge_decision, pending_pairs  # noqa: E402
from src.db.loader import load_portfolio  # noqa: E402
from src.db.resolution import apply_review, export_review, resolve_all  # noqa: E402

pytestmark = pytest.mark.db


def _load(conn, site: str, fund: str, startups: list[dict]) -> None:
    data = {"source_url": f"https://{site}/portfolio", "fund_name": fund, "startups": startups, "errors": []}
    load_portfolio(conn, data, fund_site=site, fund_name=fund, run_ref=f"{site}.json")


def _one(conn, query: str, *params):
    return conn.execute(query, params).fetchone()[0]


class ScriptedModel:
    """Answers every question the same way and keeps the prompts it was shown."""

    def __init__(self, decision, confidence, evidence):
        self.answer = {"decision": decision, "confidence": confidence, "reason": "scripted",
                       "evidence": [{"record": r, "quote": q} for r, q in evidence]}
        self.prompts: list[str] = []

    def __call__(self, messages, schema):
        self.prompts.append(messages[1]["content"])
        return self.answer


def _kilimo(conn):
    _load(conn, "theyieldlab.com", "The Yield Lab", [{"name": "Kilimo", "country": "Argentina",
        "description": "Irrigation management software for farmers"}])
    _load(conn, "gridexponential.com", "GRIDX", [{"name": "Kilimo Labs",
        "description": "Water irrigation decisions using satellite data"}])
    resolve_all(conn)
    [decision_id] = pending_pairs(conn)
    return decision_id


def _kigui(conn):
    _load(conn, "theyieldlab.com", "The Yield Lab", [{"name": "Kigüi", "country": "Mexico",
        "description": "Food & Ag Logistics & Storage Solutions"}])
    _load(conn, "newtopia.vc", "Newtopia VC", [{"name": "Kigui",
        "description": "AI agents that run retail execution, detecting out-of-stocks"}])
    resolve_all(conn)
    [decision_id] = pending_pairs(conn)
    return decision_id


def test_a_checked_match_merges_the_actors(conn):
    decision_id = _kilimo(conn)
    model = ScriptedModel("match", "high", [("A", "Irrigation management software"),
                                            ("B", "irrigation decisions using satellite data")])

    outcome, _ = judge_decision(conn, decision_id, model, "test-model")

    assert outcome == "merged"
    assert _one(conn, "SELECT count(*) FROM active_actors WHERE name LIKE 'Kilimo%%'") == 1
    assert "Listed by: The Yield Lab" in model.prompts[0] and "Listed by: GRIDX" in model.prompts[0]
    evidence, decided_by = conn.execute(
        "SELECT evidence, decided_by FROM match_decisions WHERE is_current AND method = 'llm'").fetchone()
    assert len(evidence) == 2 and decided_by == "test-model"


def test_a_rejected_mention_becomes_its_own_actor_and_stays_rejected(conn):
    decision_id = _kigui(conn)
    model = ScriptedModel("no_match", "medium", [("A", "AI agents that run retail execution"),
                                                 ("B", "Food & Ag Logistics")])

    outcome, _ = judge_decision(conn, decision_id, model, "test-model")

    assert outcome == "new actor"
    assert _one(conn, "SELECT count(*) FROM active_actors WHERE name_key = 'kigui'") == 2
    assert _one(conn, "SELECT count(*) FROM mention_relations WHERE relationship_id IS NULL") == 1   # not yet
    stats = resolve_all(conn)                     # scores the two actors again, as a new pair
    assert stats.relationships == 1
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0
    assert pending_pairs(conn) == []


def test_an_unchecked_answer_stays_for_a_person_and_is_not_asked_again(conn, tmp_path):
    decision_id = _kilimo(conn)
    model = ScriptedModel("match", "high", [("A", "Irrigation management software"),
                                            ("B", "the leading irrigation platform")])      # not in record B

    outcome, reason = judge_decision(conn, decision_id, model, "test-model")

    assert outcome == "unsure" and "not found in the records" in reason
    assert pending_pairs(conn) == []                                    # the judge doesn't loop on it
    resolve_all(conn)
    assert _one(conn, "SELECT method FROM match_decisions WHERE is_current AND decision = 'unsure'") == "llm"

    path = tmp_path / "review.csv"                                       # a person still decides it
    export_review(conn, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["reason"].startswith("LLM (match, high confidence)")
    rows[0]["your_decision"] = "match"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert apply_review(conn, path, "tester")["merged"] == 1


def test_a_failed_answer_writes_nothing(conn):
    decision_id = _kilimo(conn)
    before = _one(conn, "SELECT count(*) FROM match_decisions")

    def broken_model(messages, schema):
        raise ValueError("no JSON object in model reply")

    with pytest.raises(ValueError):
        judge_decision(conn, decision_id, broken_model, "test-model")
    assert _one(conn, "SELECT count(*) FROM match_decisions") == before
    assert pending_pairs(conn) == [decision_id]


def _id(conn, name: str) -> int:
    return _one(conn, "SELECT id FROM actors WHERE name = %s", name)


def _current(conn, decision_id: int):
    """(decision, method) now current for the pair behind DECISION_ID."""
    mention_id, actor_id, candidate_id = conn.execute(
        "SELECT mention_id, actor_id, candidate_actor_id FROM match_decisions WHERE id = %s", (decision_id,)).fetchone()
    return conn.execute(
        """SELECT decision, method FROM match_decisions WHERE is_current AND candidate_actor_id = %s
           AND mention_id IS NOT DISTINCT FROM %s AND actor_id IS NOT DISTINCT FROM %s""",
        (candidate_id, mention_id, actor_id)).fetchone()


def test_a_checked_match_links_a_waiting_mention_and_its_investment(conn):
    _load(conn, "gridexponential.com", "GRIDX", [{"name": "Puna Bio", "website": "https://puna.bio",
        "description": "Biological inputs for agriculture using extremophiles"}])
    _load(conn, "theyieldlab.com", "The Yield Lab", [{"name": "PUNA BIO CORP",
        "description": "Seed coatings that help crops grow in poor soils"}])
    resolve_all(conn)
    [decision_id] = pending_pairs(conn)
    model = ScriptedModel("match", "high", [("A", "Seed coatings that help crops grow"),
                                            ("B", "Biological inputs for agriculture")])

    outcome, _ = judge_decision(conn, decision_id, model, "test-model")

    assert outcome == "linked"
    assert _one(conn, "SELECT resolution_method FROM mentions WHERE name = 'PUNA BIO CORP'") == "llm"
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 0
    assert resolve_all(conn).relationships == 1                 # The Yield Lab -> Puna Bio
    funds = {r[0] for r in conn.execute(
        """SELECT f.name FROM relationships r JOIN actors f ON f.id = r.source_actor_id
           WHERE r.target_actor_id = %s""", (_id(conn, "Puna Bio"),)).fetchall()}
    assert funds == {"GRIDX", "The Yield Lab"}


def test_a_judged_no_match_between_actors_survives_later_runs(conn):
    decision_id = _kilimo(conn)
    model = ScriptedModel("no_match", "medium", [("B", "Water irrigation decisions using satellite data")])

    assert judge_decision(conn, decision_id, model, "test-model")[0] == "rejected"
    resolve_all(conn)
    resolve_all(conn)

    assert _current(conn, decision_id) == ("no_match", "llm")
    assert {"Kilimo", "Kilimo Labs"} <= {r[0] for r in conn.execute("SELECT name FROM active_actors").fetchall()}


def test_a_mention_waits_until_every_candidate_is_rejected(conn):
    _load(conn, "a.example", "Fund A", [{"name": "Kilimo", "country": "Argentina",
        "description": "Irrigation management software for farmers"}])
    _load(conn, "b.example", "Fund B", [{"name": "Kilimo Labs",
        "description": "Water irrigation decisions using satellite data"}])
    _load(conn, "c.example", "Fund C", [{"name": "KILIMO", "description": "Fintech for small shops"}])
    resolve_all(conn)
    mention = _one(conn, "SELECT id FROM mentions WHERE name = 'KILIMO'")
    pairs = dict(conn.execute(
        """SELECT candidate_actor_id, id FROM match_decisions
           WHERE is_current AND decision = 'unsure' AND mention_id = %s""", (mention,)).fetchall())
    assert set(pairs) == {_id(conn, "Kilimo"), _id(conn, "Kilimo Labs")}
    model = ScriptedModel("no_match", "high", [("A", "Fintech for small shops")])

    assert judge_decision(conn, pairs[_id(conn, "Kilimo Labs")], model, "m")[0] == "rejected"
    assert _one(conn, "SELECT actor_id FROM mentions WHERE id = %s", mention) is None     # Kilimo still open
    assert judge_decision(conn, pairs[_id(conn, "Kilimo")], model, "m")[0] == "new actor"
    new_actor = _one(conn, "SELECT actor_id FROM mentions WHERE id = %s", mention)
    carried = {r[0] for r in conn.execute(
        """SELECT CASE WHEN actor_id = %s THEN candidate_actor_id ELSE actor_id END FROM match_decisions
           WHERE is_current AND method = 'llm' AND decision = 'no_match' AND %s IN (actor_id, candidate_actor_id)""",
        (new_actor, new_actor)).fetchall()}
    assert carried == {_id(conn, "Kilimo"), _id(conn, "Kilimo Labs")}   # both answers carried to the new actor


def test_different_websites_keep_a_confident_match_in_review(conn):
    _load(conn, "a.example", "Fund A", [{"name": "Kigüi", "website": "https://kigui.mx",
        "description": "Food & Ag Logistics & Storage Solutions"}])
    _load(conn, "b.example", "Fund B", [{"name": "Kigui", "website": "https://kigui.ai",
        "description": "AI agents that run retail execution"}])
    resolve_all(conn)
    [decision_id] = pending_pairs(conn)
    model = ScriptedModel("match", "high", [("A", "AI agents that run retail execution"),
                                            ("B", "Food & Ag Logistics")])

    outcome, reason = judge_decision(conn, decision_id, model, "m")

    assert outcome == "unsure" and "different websites" in reason
    assert _one(conn, "SELECT count(*) FROM active_actors WHERE name_key = 'kigui'") == 1
    assert _one(conn, "SELECT count(*) FROM match_review_queue") == 1


@pytest.mark.parametrize("decided_by", ["person", "merge"])
def test_a_pair_decided_since_it_was_listed_is_skipped_without_asking(conn, tmp_path, decided_by):
    decision_id = _kilimo(conn)
    if decided_by == "person":
        path = tmp_path / "review.csv"
        export_review(conn, path)
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        rows[0]["your_decision"] = "no_match"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        apply_review(conn, path, "tester")
    else:
        from src.db.resolution import merge_actors
        merge_actors(conn, _id(conn, "Kilimo"), _id(conn, "Kilimo Labs"))
    model = ScriptedModel("match", "high", [("A", "Irrigation management software"),
                                            ("B", "irrigation decisions using satellite data")])

    assert judge_decision(conn, decision_id, model, "m") == ("skipped", "")
    assert model.prompts == []                                        # the model was not asked
    if decided_by == "person":
        assert _current(conn, decision_id) == ("no_match", "human")   # and nothing was merged
        assert {"Kilimo", "Kilimo Labs"} <= {r[0] for r in conn.execute("SELECT name FROM active_actors")}


def test_the_decision_keeps_the_score_and_the_models_answer(conn):
    decision_id = _kilimo(conn)
    score = _one(conn, "SELECT score FROM match_decisions WHERE id = %s", decision_id)
    model = ScriptedModel("unsure", "low", [("A", "Irrigation management software")])

    judge_decision(conn, decision_id, model, "llama-test")

    row = conn.execute("""SELECT score, features, reason, evidence, decided_by FROM match_decisions
                          WHERE is_current AND method = 'llm'""").fetchone()
    assert row[0] == score and row[1]["llm_answer"] == model.answer
    assert row[1]["name_strength"] == "strong"                         # the score's features are kept
    assert row[2] == "LLM (unsure, low confidence): scripted"
    assert row[3] == [{"record": "A", "quote": "Irrigation management software", "substantive": True}]
    assert row[4] == "llama-test"
    assert _one(conn, "SELECT count(*) FROM match_decisions WHERE id = %s AND NOT is_current", decision_id) == 1


def test_pending_pairs_best_first_limited_and_only_unjudged(conn):
    _kigui(conn)
    _load(conn, "a.example", "Fund A", [{"name": "Kilimo", "country": "Argentina"}])
    _load(conn, "b.example", "Fund B", [{"name": "Kilimo Labs"}])
    resolve_all(conn)
    pairs = pending_pairs(conn)
    scores = [_one(conn, "SELECT score FROM match_decisions WHERE id = %s", d) for d in pairs]

    assert len(pairs) == 2 and scores == sorted(scores, reverse=True)
    assert pending_pairs(conn, limit=1) == pairs[:1]
    judge_decision(conn, pairs[0], ScriptedModel("unsure", "low", []), "m")
    assert pending_pairs(conn) == pairs[1:]
