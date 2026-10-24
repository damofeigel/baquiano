"""Pair scoring in src/db/resolution.py on known cases from our data (no database)."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from src.db.resolution import (  # noqa: E402
    Profile, best_names, compare_names, description_overlap, plain, score_pair, trigram_similarity,
)


def _p(name: str, *, domains=(), countries=(), description=None, types=("Startup",), sources=(), id=1) -> Profile:
    return Profile("actor", id, name, [plain(name)], domains=set(domains), countries=set(countries),
                   descriptions=[description] if description else [], types=set(types), sources=set(sources))


@pytest.mark.parametrize("a, b, strength", [
    ("Botanical Solution", "Botanical Solutions", "exact"),
    ("Puna Bio", "PunaBio", "exact"),
    ("Heartbest", "Heartbest Foods", "strong"),
    ("Kilimo", "Kilimo Labs", "strong"),
    ("CONICET", "Consejo Nacional de Investigaciones Cientificas y Tecn CONICET", "partial"),
    ("Agree", "Agreena", "weak"),
    ("@Tech", "Menta Tech", "weak"),
    ("Bsafe Biotech", "ZEV Biotech", "weak"),
])
def test_name_strength(a, b, strength):
    assert compare_names(plain(a), plain(b))["name_strength"] == strength


def test_numbers_in_names():
    assert compare_names("startup 1", "startup 10")["numbers"] == "different"
    assert compare_names("glaia", "glaia 2")["numbers"] == "one_side"


def test_trigram_similarity_follows_pg_trgm():
    assert trigram_similarity("agree", "agreena") == pytest.approx(5 / 9)
    assert trigram_similarity("kilimo", "kilimo") == 1.0


def test_same_name_alone_is_not_enough():
    # Real case: Newtopia's Kigui (retail AI) and The Yield Lab's Kigüi (food logistics, Mexico)
    decision, score, _, _ = score_pair(
        _p("Kigui", description="AI agents that run retail execution, detecting out-of-stocks and stagnant stock"),
        _p("Kigüi", countries={"mexico"}, description="Food & Ag Logistics & Storage Solutions", id=2))
    assert decision == "unsure" and score == 0.7


def test_similar_name_with_corroboration_matches():
    decision, _, features, _ = score_pair(
        _p("Heartbest", countries={"mexico"}, description="Plant based dairy alternatives made with fermentation"),
        _p("Heartbest Foods", countries={"mexico"}, description="Dairy alternatives from plant based fermentation",
           id=2))
    assert decision == "match"
    assert features["name_strength"] == "strong" and features["description_overlap"] >= 0.4


def test_listed_separately_on_the_same_page_is_not_a_match():
    decision, _, _, reason = score_pair(_p("Glaia", sources={7}), _p("Glaia 2", sources={7}, id=2))
    assert decision == "no_match" and "same page" in reason


@pytest.mark.parametrize("a, b", [
    (_p("Startup 1"), _p("Startup 10", id=2)),
    (_p("Agree", countries={"argentina"}), _p("Agreena", countries={"argentina"}, id=2)),
    (_p("Kaszek", types={"Fund"}), _p("Kaszek", types={"Startup"}, id=2)),
])
def test_clear_non_matches(a, b):
    assert score_pair(a, b)[0] == "no_match"


def test_different_websites_count_against():
    decision, _, features, _ = score_pair(
        _p("Kigüi", domains={"kigui.mx"}, countries={"mexico"}),
        _p("Kigui", domains={"kigui.ai"}, countries={"mexico"}, id=2))
    assert features["domain_conflict"] and decision == "unsure"


def test_acronym_inside_full_name_goes_to_review():
    decision, _, _, _ = score_pair(
        _p("CONICET", types={"Academia"}),
        _p("CONSEJO NACIONAL DE INVESTIGACIONES CIENTIFICAS Y TECN CONICET", types={"Academia"}, id=2))
    assert decision == "unsure"


# --- Names ------------------------------------------------------------------------------

@pytest.mark.parametrize("a, b, strength", [
    # Spelling variants of one name
    ("Kigüi", "KIGUI", "exact"),                          # accents and case
    ("Puna-Bio", "Puna Bio", "exact"),                    # punctuation
    ("Biome Makers", "Biome Maker", "exact"),             # plural
    # The name plus words companies add
    ("Beeflow", "Beeflow Technologies", "strong"),
    ("Stamm", "Stamm Biotech", "strong"),
    ("Kilimo", "Kilimo Agtech Labs", "strong"),           # several added words
    ("Satellogic", "Satellogic Analytics", "strong"),
    # Related, but not enough to say
    ("Satellogic", "Satellogic Space", "partial"),        # the added word isn't a descriptor
    ("Satellogic", "Satelogic", "partial"),               # typo
    ("Puna Bio", "Bio Puna", "partial"),                  # same words, other order
    # Different names
    ("Agrofy", "Agrotoken", "weak"),
    ("Agrofy", "Agrofi", "weak"),                         # one letter apart, but short
    ("Bioceres", "Biosidus", "weak"),
    ("Tech", "Tech Labs", "weak"),                        # descriptor words only
    ("Ini", "Ini Biotech", "weak"),                       # too short to count as contained
])
def test_more_name_strengths(a, b, strength):
    assert compare_names(plain(a), plain(b))["name_strength"] == strength


@pytest.mark.parametrize("a, b, numbers", [
    ("glaia 2", "glaia 3", "different"),
    ("startup 2", "startup 2", None),
    ("kilimo", "kilimo labs", None),
])
def test_more_numbers_in_names(a, b, numbers):
    assert compare_names(a, b)["numbers"] == numbers


def test_best_alias_counts():
    actor = Profile("actor", 1, "Heartbest Foods", ["heartbest foods", "heartbest"])
    mention = Profile("mention", 2, "HEARTBEST", ["heartbest"])
    assert best_names(mention.names, actor.names)["name_strength"] == "exact"


# --- Descriptions ---------------------------------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("Plataforma de biotecnología para la agricultura sustentable",           # Spanish, accents, stopwords
     "Biotecnologia aplicada a la agricultura sustentable en Argentina", 1.0),
    ("Pollination technologies using bees and data",
     "Precision pollination with bees, sensors and data", 0.75),
    ("Genome editing for crops and livestock improvement",
     "Software for restaurants and delivery orders", 0.0),
    ("Satellite imagery", "Satellite imagery for agriculture", None),          # too short to tell
])
def test_description_overlap(a, b, expected):
    assert description_overlap([a], [b]) == expected


# --- Decisions ------------------------------------------------------------------------------

POLLINATION = "Precision pollination with bees, sensors and data for growers"
POLLINATION_2 = "Pollination technologies using bees, sensors and data"


@pytest.mark.parametrize("a, b, decision, score", [
    # Exact name: descriptions or descriptions plus country are enough...
    (_p("Beeflow", description=POLLINATION), _p("BEEFLOW", description=POLLINATION_2, id=2), "match", 0.9),
    (_p("Beeflow", countries={"argentina"}, description=POLLINATION),
     _p("Beeflow", countries={"argentina"}, description=POLLINATION_2, id=2), "match", 0.95),
    # ...the country alone isn't
    (_p("Beeflow", countries={"argentina"}), _p("Beeflow", countries={"argentina"}, id=2), "unsure", 0.75),
    # Name plus a descriptor word: needs both descriptions and country
    (_p("Beeflow", description=POLLINATION), _p("Beeflow Technologies", description=POLLINATION_2, id=2),
     "unsure", 0.8),
    (_p("Beeflow", countries={"argentina"}), _p("Beeflow Labs", countries={"argentina"}, id=2), "unsure", 0.65),
    # A partial name never matches on its own evidence
    (_p("Satellogic", countries={"argentina"}, description=POLLINATION),
     _p("Satellogic Space", countries={"argentina"}, description=POLLINATION_2, id=2), "unsure", 0.65),
    # A weak name with everything else agreeing: maybe a rename, so a person looks
    (_p("Agrofy", countries={"argentina"}, description=POLLINATION),
     _p("Agrotoken", countries={"argentina"}, description=POLLINATION_2, id=2), "unsure", 0.35),
    (_p("Agrofy"), _p("Agrotoken", id=2), "no_match", 0.1),
])
def test_decision_and_score(a, b, decision, score):
    got_decision, got_score, _, _ = score_pair(a, b)
    assert (got_decision, got_score) == (decision, score)


@pytest.mark.parametrize("against, reason", [
    ({"domains": ({"beeflow.com"}, {"beeflow.ar"})}, "different websites"),
    ({"countries": ({"argentina"}, {"chile"})}, "different countries"),
    ({"sources": ({3}, {3})}, "same page"),
])
def test_anything_against_blocks_a_match(against, reason):
    """Exact name and overlapping descriptions would match, but one piece of evidence says otherwise."""
    left = dict(description=POLLINATION)
    right = dict(description=POLLINATION_2, id=2)
    for key, (x, y) in against.items():
        left[key], right[key] = x, y
    decision, _, _, got_reason = score_pair(_p("Beeflow", **left), _p("Beeflow", **right))
    assert decision != "match" and reason in got_reason


def test_shared_website_is_proof_even_with_other_names():
    decision, score, _, reason = score_pair(_p("Agrofy", domains={"agrofy.com"}),
                                            _p("Agrofy Global Marketplace", domains={"agrofy.com"}, id=2))
    assert (decision, score, reason) == ("match", 1.0, "Same website")


@pytest.mark.parametrize("types_a, types_b, conflict", [
    ({"Academia"}, {"Startup"}, True),
    ({"Startup", "Corporation"}, {"Corporation"}, False),    # one shared type is enough
    ((), {"Fund"}, False),                                   # unknown type isn't a conflict
])
def test_type_conflict(types_a, types_b, conflict):
    _, _, features, _ = score_pair(_p("Bioceres", types=types_a), _p("Bioceres", types=types_b, id=2))
    assert features["type_conflict"] is conflict


def test_score_never_goes_below_zero():
    _, score, _, _ = score_pair(
        _p("Glaia", domains={"glaia.com"}, countries={"uk"}, sources={1}),
        _p("Glaia 2", domains={"hexafly.ie"}, countries={"ireland"}, sources={1}, id=2))
    assert score == 0.0


@pytest.mark.parametrize("a, b", [
    (_p("Heartbest", countries={"mexico"}, description=POLLINATION),
     _p("Heartbest Foods", countries={"mexico"}, description=POLLINATION_2, id=2)),
    (_p("CONICET"), _p("Consejo Nacional de Investigaciones Cientificas y Tecnicas CONICET", id=2)),
    (_p("Kigüi", domains={"kigui.mx"}), _p("Kigui", domains={"kigui.ai"}, id=2)),
    (_p("Glaia", sources={1}), _p("Glaia 2", sources={1}, id=2)),
])
def test_order_of_the_pair_does_not_matter(a, b):
    assert score_pair(a, b)[:2] == score_pair(b, a)[:2]
