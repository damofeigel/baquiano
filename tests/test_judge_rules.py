"""The LLM judge's answer checks in src/db/judge.py (no database, no model)."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from src.db.judge import JUDGE_SCHEMA, SYSTEM_PROMPT, build_messages, interpret, record_lines  # noqa: E402
from src.db.resolution import Profile  # noqa: E402

KILIMO = Profile("actor", 1, "Kilimo", ["kilimo"], domains={"kilimo.com"}, countries={"argentina"},
                 descriptions=["Irrigation management software for farmers"], funds={"The Yield Lab"},
                 types={"Startup"}, mentions=1)
KILIMO_LABS = Profile("actor", 2, "Kilimo Labs", ["kilimo labs"], countries={"argentina"}, descriptions=[
    "Water irrigation decisions using satellite data"], funds={"GRIDX"}, types={"Startup"}, mentions=1,
    aliases=["KILIMO LABS SA"])
A_TEXT, B_TEXT = "Irrigation management software", "irrigation decisions using satellite data"


def _answer(decision="match", confidence="high", evidence=(("A", A_TEXT), ("B", B_TEXT)),
            reason: str | None = "Both irrigation."):
    return {"decision": decision, "confidence": confidence, "reason": reason,
            "evidence": [{"record": r, "quote": q} for r, q in evidence]}


def _decide(answer, features=None, left=KILIMO, right=KILIMO_LABS):
    return interpret(answer, left, right, features or {})


# --- Match ---------------------------------------------------------------------------------

def test_confident_match_with_quotes_from_both_records_is_applied():
    decision, evidence, reason = _decide(_answer())
    assert decision == "match" and [q["record"] for q in evidence] == ["A", "B"]
    assert all(q["substantive"] for q in evidence)
    assert reason == "LLM (match, high confidence): Both irrigation."


def test_a_website_counts_as_substantive():
    assert _decide(_answer(evidence=[("A", "kilimo.com"), ("B", B_TEXT)]))[0] == "match"


@pytest.mark.parametrize("answer, features, why", [
    (_answer(confidence="medium"), {}, "needs high confidence"),
    (_answer(confidence="low"), {}, "needs high confidence"),
    (_answer(evidence=[("A", A_TEXT)]), {}, "a quote from each record's description or website"),
    (_answer(evidence=[("A", A_TEXT), ("A", "software for farmers")]), {}, "from each record"),
    (_answer(evidence=[]), {}, "from each record"),
    (_answer(), {"domain_conflict": True}, "different websites"),
    (_answer(), {"same_page": True}, "listed on the same page"),
    (_answer(), {"country": "conflict"}, "different countries"),
])
def test_a_match_that_misses_the_bar_stays_unsure(answer, features, why):
    decision, _, reason = _decide(answer, features)
    assert decision == "unsure" and why in reason


def test_every_reason_a_match_was_not_applied_is_listed():
    _, _, reason = _decide(_answer(confidence="medium", evidence=[("A", A_TEXT)]),
                           {"domain_conflict": True, "same_page": True})
    assert reason.endswith("[not applied: needs high confidence and a quote from each record's description "
                           "or website; different websites, listed on the same page]")


@pytest.mark.parametrize("evidence", [
    [("A", "Kilimo"), ("B", "Kilimo Labs")],                  # the names: same name is not enough
    [("A", "Kilimo"), ("B", "KILIMO LABS SA")],               # an alias
    [("A", "argentina"), ("B", "argentina")],                 # the country
    [("A", "Startup"), ("B", "Startup")],                     # the type
    [("A", "The Yield Lab"), ("B", "GRIDX")],                 # the funds
])
def test_quotes_that_prove_nothing_do_not_make_a_match(evidence):
    decision, verified, reason = _decide(_answer(evidence=evidence))
    assert decision == "unsure" and "description or website" in reason
    assert len(verified) == 2 and not any(q["substantive"] for q in verified)   # real, but not enough


# --- Quotes --------------------------------------------------------------------------------

def test_quotes_must_be_in_the_record_they_are_attributed_to():
    answer = _answer(evidence=[("A", A_TEXT),
                               ("B", A_TEXT),                                  # that's A's text, not B's
                               ("B", "pioneers of precision irrigation")])     # in neither record
    decision, evidence, reason = _decide(answer)
    assert decision == "unsure" and evidence == [{"record": "A", "quote": A_TEXT, "substantive": True}]
    assert "2 quote(s) not found in the records" in reason


@pytest.mark.parametrize("quote", [
    "Kind: an organization known from 1 record(s)",          # the prompt's own labels
    "an organization known from",
    "none known", "not stated", "Descriptions:", "Record B",
    "Automatic name comparison",
])
def test_text_the_prompt_adds_is_not_evidence(quote):
    _, evidence, reason = _decide(_answer(evidence=[("A", A_TEXT), ("B", quote)]))
    assert [q["record"] for q in evidence] == ["A"] and "1 quote(s) not found" in reason


@pytest.mark.parametrize("quote", [
    "IRRIGATION   decisions using SATELLITE data",           # case and spacing
    "irrigation decisions using satellite data.",            # trailing full stop
    '"irrigation decisions using satellite data"',           # quoted
])
def test_quotes_are_compared_loosely(quote):
    assert _decide(_answer(evidence=[("A", A_TEXT), ("B", quote)]))[0] == "match"


def test_accents_do_not_matter():
    left = Profile("mention", 1, "Agrobit", ["agrobit"], descriptions=["Plataforma de biotecnología agrícola"])
    right = Profile("actor", 2, "Agrobit SA", ["agrobit sa"], descriptions=["Biotecnologia agricola para el campo"])
    answer = _answer(evidence=[("A", "plataforma de biotecnologia agricola"), ("B", "Biotecnología agrícola")])
    assert _decide(answer, left=left, right=right)[0] == "match"


@pytest.mark.parametrize("evidence", [
    [{"record": "A", "quote": "Irr"}],                       # under 4 characters
    [{"record": "C", "quote": A_TEXT}],                      # no such record
    [{"record": "A"}],                                       # no quote
    [{"record": "A", "quote": None}],
    [{"record": "A", "quote": 42}],
    ["Irrigation management software"],                      # not an object
    [None],
])
def test_malformed_evidence_is_not_counted_and_never_crashes(evidence):
    decision, verified, reason = interpret({"decision": "no_match", "confidence": "high", "reason": "x",
                                            "evidence": evidence}, KILIMO, KILIMO_LABS, {})
    assert decision == "unsure" and verified == [] and "1 quote(s) not found" in reason


# --- No match and unsure -------------------------------------------------------------------

@pytest.mark.parametrize("answer, decision", [
    (_answer("no_match", "high", [("A", A_TEXT)]), "no_match"),
    (_answer("no_match", "medium", [("B", B_TEXT)]), "no_match"),          # one record is enough
    (_answer("no_match", "high", []), "unsure"),                           # no evidence
    (_answer("no_match", "low", [("A", A_TEXT)]), "unsure"),
    (_answer("no_match", "high", [("A", "Kilimo"), ("B", "Kilimo Labs")]), "unsure"),   # names only
    (_answer("unsure", "high"), "unsure"),
])
def test_no_match_and_unsure(answer, decision):
    assert _decide(answer)[0] == decision


def test_conflicts_do_not_block_a_no_match():
    assert _decide(_answer("no_match", "high", [("A", A_TEXT)]), {"domain_conflict": True})[0] == "no_match"


@pytest.mark.parametrize("answer", [
    {},                                                                    # empty reply
    {"decision": "maybe", "confidence": "high", "reason": "?", "evidence": []},
    {"decision": "match", "reason": "no confidence given", "evidence": [{"record": "A", "quote": A_TEXT}]},
    {"decision": "MATCH", "confidence": "HIGH", "reason": "upper case", "evidence": []},
])
def test_odd_answers_stay_unsure(answer):
    assert _decide(answer)[0] == "unsure"


def test_reason_is_trimmed_and_may_be_empty():
    assert _decide(_answer("unsure", "low", reason="  needs more data \n"))[2] == \
        "LLM (unsure, low confidence): needs more data"
    assert _decide(_answer("unsure", "low", reason=None))[2] == "LLM (unsure, low confidence): "


# --- Prompt and schema -----------------------------------------------------------------------

def test_the_prompt_shows_both_records_and_what_is_missing():
    user = build_messages(KILIMO, KILIMO_LABS, "strong name match")[1]["content"]
    assert user.startswith("Record A\nKind: an organization known from 1 record(s)\nName: Kilimo\n")
    assert "\n\nRecord B\nKind: an organization known from 1 record(s)\nName: Kilimo Labs\n" in user
    assert "Also written as: KILIMO LABS SA" in user
    assert "Websites: kilimo.com" in user and "Websites: none known" in user
    assert '- "Water irrigation decisions using satellite data"' in user
    assert user.endswith("Automatic name comparison: strong name match")


def test_every_description_gets_its_own_line():
    p = Profile("actor", 3, "Beeflow", ["beeflow"], descriptions=["Pollination technologies", "Bees as a service"],
                funds={"The Yield Lab", "GRIDX"}, mentions=2)
    lines = record_lines(p)
    assert lines[-3:] == ["Descriptions:", '- "Pollination technologies"', '- "Bees as a service"']
    assert "Listed by: GRIDX, The Yield Lab" in lines                        # sorted
    assert "Kind: an organization known from 2 record(s)" in lines


def test_a_mention_is_described_as_a_single_record():
    mention = Profile("mention", 9, "Kigui", ["kigui"], mentions=1)
    lines = record_lines(mention)
    assert lines[:2] == ["Kind: a single record, from one fund's page", "Name: Kigui"]
    assert "Descriptions: none" in lines and not any(line.startswith(("Type", "Listed by")) for line in lines)


def test_missing_automatic_comparison_is_said():
    assert build_messages(KILIMO, KILIMO_LABS, "")[1]["content"].endswith("Automatic name comparison: none")


def test_system_prompt_warns_that_a_name_is_not_enough():
    system = build_messages(KILIMO, KILIMO_LABS, "x")[0]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert "NOT enough" in SYSTEM_PROMPT and "Kigüi" in SYSTEM_PROMPT


def test_schema_is_strict_and_closed():
    assert set(JUDGE_SCHEMA["required"]) == set(JUDGE_SCHEMA["properties"])
    assert JUDGE_SCHEMA["additionalProperties"] is False
    assert JUDGE_SCHEMA["properties"]["decision"]["enum"] == ["match", "no_match", "unsure"]
    item = JUDGE_SCHEMA["properties"]["evidence"]["items"]
    assert item["properties"]["record"]["enum"] == ["A", "B"] and item["additionalProperties"] is False
