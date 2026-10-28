"""Entity resolution v1: candidates by similar name, scored, then decided.

Two kinds of pairs:

  * a MENTION the loader left unresolved (same name key as an actor but no
    shared identifier) against its candidate actors;
  * two ACTORS that may be the same: the loader creates a new actor whenever
    nothing matched exactly, so 'Heartbest' and 'Heartbest Foods' start apart.

Candidates come from trigram similarity on normalized names and aliases
(pg_trgm). Each pair gets features (name, website, description, country, same
listing page, type) and a score. The decision is

  * match    -> the mention is linked / the actors are merged (non-destructively:
                the duplicate gets status 'merged' and merged_into_id)
  * no_match -> recorded, so the pair is not proposed again
  * unsure   -> MATCH_REVIEW_QUEUE: export it to CSV, decide, apply

The same name alone never merges: a match needs corroboration (overlapping
descriptions or the same country) and nothing against it. A human decision is
never overridden.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from .loader import GENERIC_DOMAINS, _add_alias, create_actor_for_mention, link_mention, materialize_relationships

# Blocking: pairs below these are never compared
SIMILARITY_THRESHOLD = 0.45        # similarity() between two names
CONTAINMENT_THRESHOLD = 0.8        # word_similarity(): a short name inside a longer one
MIN_CONTAINED_LENGTH = 4           # so 'ini' isn't found inside everything

MATCH_AT = 0.85
NO_MATCH_BELOW = 0.35
NAME_POINTS = {"exact": 0.7, "strong": 0.6, "partial": 0.4, "weak": 0.1}
NAME_RANK = {"weak": 0, "partial": 1, "strong": 2, "exact": 3}

# Words a company adds to its name without becoming another company
DESCRIPTOR_WORDS = {
    "food", "foods", "lab", "labs", "bio", "biotech", "tech", "technologies", "technology", "ai", "ag", "agro",
    "agtech", "health", "energy", "solution", "systems", "system", "group", "global", "latam", "argentina",
    "app", "io", "hq", "therapeutic", "materials", "robotics", "analytics", "data", "ventures", "studio",
}

STOPWORDS = {
    # English
    "with", "that", "this", "from", "their", "they", "have", "into", "more", "than", "which", "where", "while",
    "also", "using", "based", "through", "company", "startup", "platform", "solution", "solutions", "develops",
    "developing", "provides", "offers", "world", "first", "help", "helps", "make", "makes", "your", "will",
    # Spanish
    "para", "como", "desde", "sobre", "entre", "este", "esta", "estos", "estas", "empresa", "plataforma",
    "solucion", "soluciones", "desarrolla", "brinda", "ofrece", "nuestra", "nuestro", "permite", "traves",
}


# --- Pair features (pure) --------------------------------------------------------------

@dataclass
class Profile:
    """One side of a pair: a mention, or an actor with everything known about it."""
    kind: str                     # 'mention' | 'actor'
    id: int
    display: str
    names: list[str]              # normalized (name_norm / alias_norm)
    domains: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    descriptions: list[str] = field(default_factory=list)
    types: set[str] = field(default_factory=set)
    sources: set[int] = field(default_factory=set)
    funds: set[str] = field(default_factory=set)
    mentions: int = 0
    aliases: list[str] = field(default_factory=list)   # names as written, for people and the LLM judge


def plain(text: str) -> str:
    """Lowercase, no accents, words separated by single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


# Names are compared as stems ('technologies' -> 'technologie'), so the descriptor words are too
DESCRIPTOR_STEMS = {_stem(w) for w in DESCRIPTOR_WORDS}


def trigrams(text: str) -> set[str]:
    """The trigrams pg_trgm uses: each word padded with two spaces before, one after."""
    out: set[str] = set()
    for word in plain(text).split():
        padded = f"  {word} "
        out |= {padded[i:i + 3] for i in range(len(padded) - 2)}
    return out


def trigram_similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def compare_names(a: str, b: str) -> dict[str, Any]:
    """How two normalized names relate: strength exact > strong > partial > weak."""
    ta, tb = a.split(), b.split()
    sa, sb = [_stem(t) for t in ta], [_stem(t) for t in tb]
    similarity = round(trigram_similarity(a, b), 2)
    nums_a = {t for t in ta if t.isdigit()}
    nums_b = {t for t in tb if t.isdigit()}
    if nums_a and nums_b and nums_a != nums_b:
        numbers = "different"                  # 'Startup 1' / 'Startup 10'
    elif bool(nums_a) != bool(nums_b):
        numbers = "one_side"                   # 'Glaia' / 'Glaia 2'
    else:
        numbers = None

    short, long = sorted((sa, sb), key=len)
    extra = [t for t in long if t not in short]
    distinctive = "".join(t for t in short if not t.isdigit() and t not in DESCRIPTOR_STEMS)   # not just 'tech'
    contained = set(short) <= set(long) and len(distinctive) >= MIN_CONTAINED_LENGTH
    if sa == sb or a.replace(" ", "") == b.replace(" ", ""):
        strength = "exact"
    elif contained and extra and all(t in DESCRIPTOR_STEMS for t in extra):
        strength = "strong"                    # 'Heartbest' / 'Heartbest Foods'
    elif contained or similarity >= 0.6:
        strength = "partial"                   # 'CONICET' / 'Consejo Nacional ... CONICET'
    else:
        strength = "weak"                      # 'Agree' / 'Agreena'
    return {"names": [a, b], "name_strength": strength, "name_similarity": similarity,
            "contained": contained, "numbers": numbers}


def best_names(a_names: list[str], b_names: list[str]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for x in a_names:
        for y in b_names:
            f = compare_names(x, y)
            if best is None or (NAME_RANK[f["name_strength"]], f["name_similarity"]) > (
                    NAME_RANK[best["name_strength"]], best["name_similarity"]):
                best = f
    return best or {"names": [], "name_strength": "weak", "name_similarity": 0.0, "contained": False,
                    "numbers": None}


def _content_words(text: str) -> set[str]:
    return {_stem(w) for w in plain(text).split() if len(w) >= 4 and w not in STOPWORDS and not w.isdigit()}


def description_overlap(a: list[str], b: list[str]) -> float | None:
    """Shared content words over the shorter description (best pair); None if too short to tell."""
    best = None
    for x in a:
        for y in b:
            wx, wy = _content_words(x), _content_words(y)
            if min(len(wx), len(wy)) < 3:
                continue
            overlap = len(wx & wy) / min(len(wx), len(wy))
            best = overlap if best is None else max(best, overlap)
    return None if best is None else round(best, 2)


def score_pair(a: Profile, b: Profile) -> tuple[str, float, dict[str, Any], str]:
    """(decision, score, features, reason) for two profiles."""
    f = best_names(a.names, b.names)
    f["same_domain"] = bool(a.domains & b.domains)
    f["domain_conflict"] = bool(a.domains and b.domains and not a.domains & b.domains)
    f["domains"] = [sorted(a.domains), sorted(b.domains)]
    f["description_overlap"] = description_overlap(a.descriptions, b.descriptions)
    f["country"] = (None if not (a.countries and b.countries)
                    else "agree" if a.countries & b.countries else "conflict")
    f["same_page"] = bool(a.sources & b.sources)
    f["type_conflict"] = bool(a.types and b.types and not a.types & b.types)

    if f["same_domain"]:
        return "match", 1.0, f, "Same website"
    if f["type_conflict"]:
        return "no_match", 0.0, f, f"Different types ({', '.join(sorted(a.types))} / {', '.join(sorted(b.types))})"
    if f["numbers"] == "different":
        return "no_match", 0.0, f, "Names differ by a number"

    score = NAME_POINTS[f["name_strength"]]
    reasons = [f"{f['name_strength']} name match"]
    overlap = f["description_overlap"]
    if overlap is not None and overlap >= 0.4:
        score += 0.2
        reasons.append(f"descriptions overlap ({overlap})")
    elif overlap is not None and overlap >= 0.2:
        score += 0.1
        reasons.append(f"descriptions partly overlap ({overlap})")
    if f["country"] == "agree":
        score += 0.05
        reasons.append("same country")
    elif f["country"] == "conflict":
        score -= 0.3
        reasons.append("different countries")
    if f["domain_conflict"]:
        score -= 0.3
        reasons.append("different websites")
    if f["same_page"]:
        score -= 0.4
        reasons.append("listed separately on the same page")
    if f["numbers"] == "one_side":
        score -= 0.2
        reasons.append("only one name has a number")
    score = round(min(max(score, 0.0), 1.0), 2)

    corroborated = (overlap or 0) >= 0.2 or f["country"] == "agree"
    against = f["domain_conflict"] or f["same_page"] or f["country"] == "conflict" or f["numbers"]
    if score >= MATCH_AT and corroborated and not against:
        decision = "match"
    elif score < NO_MATCH_BELOW:
        decision = "no_match"
    else:
        decision = "unsure"
    return decision, score, f, "; ".join(reasons)


# --- Profiles from the database -------------------------------------------------------------

def _country(value: str | None) -> str | None:
    return plain(value) or None if value else None


def canonical(conn: psycopg.Connection, actor_id: int) -> int:
    """Follow merged_into_id to the actor that's still active."""
    seen = set()
    while actor_id not in seen:
        seen.add(actor_id)
        row = conn.execute("SELECT merged_into_id FROM actors WHERE id = %s", (actor_id,)).fetchone()
        if row is None or row[0] is None:
            return actor_id
        actor_id = row[0]
    return actor_id


def actor_profile(conn: psycopg.Connection, actor_id: int) -> Profile:
    name, name_norm, description = conn.execute(
        "SELECT name, name_norm, description FROM actors WHERE id = %s", (actor_id,)).fetchone()
    names = [name_norm] + [r[0] for r in conn.execute(
        "SELECT DISTINCT alias_norm FROM actor_aliases WHERE actor_id = %s AND alias_norm <> ''", (actor_id,)
    ).fetchall() if r[0] != name_norm]
    p = Profile("actor", actor_id, name, [n for n in names if n])
    p.aliases = [r[0] for r in conn.execute(
        "SELECT alias FROM actor_aliases WHERE actor_id = %s ORDER BY id", (actor_id,)).fetchall() if r[0] != name]
    p.domains = {r[0] for r in conn.execute(
        "SELECT value FROM actor_identifiers WHERE actor_id = %s AND scheme = 'domain'", (actor_id,)).fetchall()}
    if description:
        p.descriptions.append(description)
    for country, desc, type_hint, source_id in conn.execute(
            "SELECT country, description, actor_type_hint, source_id FROM mentions WHERE actor_id = %s",
            (actor_id,)).fetchall():
        p.mentions += 1
        if _country(country):
            p.countries.add(_country(country))
        if desc and desc not in p.descriptions:
            p.descriptions.append(desc)
        if type_hint:
            p.types.add(type_hint)
        if source_id is not None:
            p.sources.add(source_id)
    p.types |= {r[0] for r in conn.execute(
        """SELECT t.name FROM actor_type_assignments ta JOIN actor_types t ON t.id = ta.actor_type_id
           WHERE ta.actor_id = %s""", (actor_id,)).fetchall()}
    p.funds = {r[0] for r in conn.execute(
        """SELECT DISTINCT f.name FROM relationships r JOIN actors f ON f.id = r.source_actor_id
           JOIN relationship_types t ON t.id = r.relationship_type_id
           WHERE r.target_actor_id = %s AND t.name = 'Investment'""", (actor_id,)).fetchall()}
    return p


def mention_profile(conn: psycopg.Connection, mention_id: int) -> Profile:
    name, name_norm, domain, country, description, type_hint, source_id = conn.execute(
        """SELECT name, name_norm, domain, country, description, actor_type_hint, source_id
           FROM mentions WHERE id = %s""", (mention_id,)).fetchone()
    p = Profile("mention", mention_id, name, [name_norm] if name_norm else [], mentions=1)
    if domain and domain not in GENERIC_DOMAINS:
        p.domains.add(domain)
    if _country(country):
        p.countries.add(_country(country))
    if description:
        p.descriptions.append(description)
    if type_hint:
        p.types.add(type_hint)
    if source_id is not None:
        p.sources.add(source_id)
    p.funds = {r[0] for r in conn.execute(
        """SELECT sm.name FROM mention_relations r JOIN mentions sm ON sm.id = r.source_mention_id
           WHERE r.target_mention_id = %s""", (mention_id,)).fetchall()}
    return p


# --- Decisions -

def _set_thresholds(conn: psycopg.Connection) -> None:
    conn.execute(
        sql.SQL("SET pg_trgm.similarity_threshold = {}").format(
            sql.Literal(SIMILARITY_THRESHOLD)
        )
    )
    conn.execute(
        sql.SQL("SET pg_trgm.word_similarity_threshold = {}").format(
            sql.Literal(CONTAINMENT_THRESHOLD)
        )
    )

def current_decision(conn: psycopg.Connection, *, mention_id: int | None = None, actor_id: int | None = None,
                     candidate_id: int) -> tuple[int, str, str, float | None] | None:
    """(id, decision, method, score) of the current decision for the pair, if any."""
    if mention_id is not None:
        return conn.execute(
            """SELECT id, decision, method, score FROM match_decisions
               WHERE is_current AND mention_id = %s AND candidate_actor_id = %s""",
            (mention_id, candidate_id)).fetchone()
    return conn.execute(
        """SELECT id, decision, method, score FROM match_decisions
           WHERE is_current AND actor_id = %s AND candidate_actor_id = %s""",
        (actor_id, candidate_id)).fetchone()


# Who may replace whose decision: a person over the LLM judge, the judge over scores and rules
METHOD_RANK = {"rule": 0, "score": 0, "llm": 1, "agent": 1, "human": 2}


def record_decision(conn: psycopg.Connection, *, mention_id: int | None = None, actor_id: int | None = None,
                    candidate_id: int, decision: str, method: str, score: float | None,
                    features: dict[str, Any], reason: str, decided_by: str | None = None,
                    evidence: list[dict[str, Any]] | None = None) -> int | None:
    """Make this the current decision for the pair. Returns its id, or None when a
    higher-ranked method already decided the pair (METHOD_RANK: a score never
    overrides the LLM judge, nothing overrides a person)."""
    current = current_decision(conn, mention_id=mention_id, actor_id=actor_id, candidate_id=candidate_id)
    if current:
        current_id, current_decision_, current_method, current_score = current
        if METHOD_RANK[current_method] > METHOD_RANK[method]:
            return None
        if (current_decision_, current_method) == (decision, method) and current_score == score:
            return current_id
        conn.execute("UPDATE match_decisions SET is_current = FALSE WHERE id = %s", (current_id,))
    return conn.execute(
        """INSERT INTO match_decisions (mention_id, actor_id, candidate_actor_id, decision, method, score,
                                        features, reason, evidence, decided_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (mention_id, actor_id, candidate_id, decision, method, score, json.dumps(features, ensure_ascii=False),
         reason, json.dumps(evidence or [], ensure_ascii=False), decided_by)).fetchone()[0]


def _retire_open_decisions(conn: psycopg.Connection, mention_id: int) -> None:
    """A resolved mention leaves the review queue."""
    conn.execute(
        """UPDATE match_decisions SET is_current = FALSE
           WHERE is_current AND mention_id = %s AND decision = 'unsure'""", (mention_id,))


# Tables that tie something to an actor, with the column completing their key
LINK_TABLES = {
    "actor_type_assignments": "actor_type_id",
    "actor_technological_areas": "technological_area_id",
    "actor_capabilities": "capability_id",
    "actor_sources": "source_id",
    "actor_locations": "location_id",
    "actor_patents": "patent_id",
    "actor_funding_sources": "funding_source_id",
}


def merge_actors(conn: psycopg.Connection, keep: int, drop: int) -> None:
    """Merge DROP into KEEP. DROP stays as a row (status 'merged', merged_into_id)
    and keeps whatever KEEP already had; everything else moves to KEEP."""
    params = {"keep": keep, "drop": drop}
    for table, key in LINK_TABLES.items():
        conn.execute(sql.SQL(
            """UPDATE {t} x SET actor_id = %(keep)s WHERE actor_id = %(drop)s
               AND NOT EXISTS (SELECT 1 FROM {t} y WHERE y.actor_id = %(keep)s AND y.{k} = x.{k})"""
        ).format(t=sql.Identifier(table), k=sql.Identifier(key)), params)
    conn.execute(
        """UPDATE actor_aliases x SET actor_id = %(keep)s WHERE actor_id = %(drop)s
           AND NOT EXISTS (SELECT 1 FROM actor_aliases y WHERE y.actor_id = %(keep)s
                           AND lower(y.alias) = lower(x.alias))""", params)
    conn.execute(
        """UPDATE actor_classifications SET actor_id = %(keep)s WHERE actor_id = %(drop)s
           AND NOT EXISTS (SELECT 1 FROM actor_classifications WHERE actor_id = %(keep)s)""", params)
    conn.execute("UPDATE actor_identifiers SET actor_id = %(keep)s WHERE actor_id = %(drop)s", params)
    conn.execute("UPDATE mentions SET actor_id = %(keep)s WHERE actor_id = %(drop)s", params)
    conn.execute("UPDATE funding_events SET recipient_actor_id = %(keep)s WHERE recipient_actor_id = %(drop)s",
                 params)
    conn.execute(
        """UPDATE funding_event_participants x SET investor_actor_id = %(keep)s WHERE investor_actor_id = %(drop)s
           AND NOT EXISTS (SELECT 1 FROM funding_event_participants y
                           WHERE y.investor_actor_id = %(keep)s AND y.funding_event_id = x.funding_event_id)""",
        params)
    for side, other in (("source_actor_id", "target_actor_id"), ("target_actor_id", "source_actor_id")):
        conn.execute(sql.SQL(
            """UPDATE relationships r SET {side} = %(keep)s, updated_at = NOW()
               WHERE {side} = %(drop)s AND {other} <> %(keep)s
               AND NOT EXISTS (SELECT 1 FROM relationships y WHERE y.{side} = %(keep)s
                               AND y.{other} = r.{other} AND y.relationship_type_id = r.relationship_type_id)"""
        ).format(side=sql.Identifier(side), other=sql.Identifier(other)), params)

    name = conn.execute("SELECT name FROM actors WHERE id = %(drop)s", params).fetchone()[0]
    _add_alias(conn, keep, name, None)
    conn.execute(
        """UPDATE actors k SET website = coalesce(k.website, d.website),
                               description = coalesce(k.description, d.description), updated_at = NOW()
           FROM actors d WHERE k.id = %(keep)s AND d.id = %(drop)s""", params)
    conn.execute("UPDATE actors SET merged_into_id = %(keep)s WHERE merged_into_id = %(drop)s", params)
    conn.execute(
        "UPDATE actors SET status = 'merged', merged_into_id = %(keep)s, updated_at = NOW() WHERE id = %(drop)s",
        params)
    # Open questions about DROP are re-asked about KEEP on the next run
    conn.execute(
        """UPDATE match_decisions SET is_current = FALSE
           WHERE is_current AND decision = 'unsure' AND (actor_id = %(drop)s OR candidate_actor_id = %(drop)s)""",
        params)


def _survivor(conn: psycopg.Connection, a: int, b: int) -> tuple[int, int]:
    """(keep, drop): the actor with more mentions survives, else the older one."""
    rows = conn.execute(
        """SELECT a.id FROM actors a WHERE a.id IN (%s, %s)
           ORDER BY (SELECT count(*) FROM mentions m WHERE m.actor_id = a.id) DESC, a.id""", (a, b)).fetchall()
    return rows[0][0], rows[1][0]


# --- Runs ------

@dataclass
class ResolveStats:
    pairs_scored: int = 0
    merged: int = 0
    linked: int = 0
    new_actors: int = 0
    unsure: int = 0
    no_match: int = 0
    relationships: int = 0
    notes: list[str] = field(default_factory=list)


def actor_pair_candidates(conn: psycopg.Connection) -> list[tuple[int, int]]:
    """Pairs of active actors with a similar name or alias."""
    _set_thresholds(conn)
    return conn.execute(
        """SELECT DISTINCT least(x.actor_id, y.actor_id), greatest(x.actor_id, y.actor_id)
           FROM actor_aliases x
           JOIN actors ax ON ax.id = x.actor_id AND ax.status = 'active'
           JOIN actor_aliases y ON y.actor_id <> x.actor_id
                AND (y.alias_norm %% x.alias_norm
                     OR (length(x.alias_norm) >= %s AND x.alias_norm <%% y.alias_norm))
           JOIN actors ay ON ay.id = y.actor_id AND ay.status = 'active'
           ORDER BY 1, 2""", (MIN_CONTAINED_LENGTH,)).fetchall()


def mention_candidates(conn: psycopg.Connection, mention_id: int) -> list[int]:
    """Active actors similar to an unresolved mention, plus the ones the loader proposed."""
    _set_thresholds(conn)
    rows = conn.execute(
        """SELECT DISTINCT a.id FROM mentions m
           JOIN actor_aliases al ON (al.alias_norm %% m.name_norm
                OR (length(m.name_norm) >= %s AND m.name_norm <%% al.alias_norm)
                OR (length(al.alias_norm) >= %s AND al.alias_norm <%% m.name_norm))
           JOIN actors a ON a.id = al.actor_id AND a.status = 'active'
           WHERE m.id = %s""", (MIN_CONTAINED_LENGTH, MIN_CONTAINED_LENGTH, mention_id)).fetchall()
    proposed = conn.execute(
        "SELECT DISTINCT candidate_actor_id FROM match_decisions WHERE mention_id = %s AND is_current",
        (mention_id,)).fetchall()
    found = {r[0] for r in rows} | {canonical(conn, r[0]) for r in proposed}
    return sorted(a for a in found if conn.execute(
        "SELECT status FROM actors WHERE id = %s"
        , (a,)).fetchone()[0] == "active")


def resolve_actor_pairs(conn: psycopg.Connection, stats: ResolveStats) -> None:
    for a, b in actor_pair_candidates(conn):
        a, b = canonical(conn, a), canonical(conn, b)
        if a == b:
            continue
        a, b = min(a, b), max(a, b)
        current = current_decision(conn, actor_id=a, candidate_id=b)
        if current and METHOD_RANK[current[2]] > METHOD_RANK["score"]:
            continue
        decision, score, features, reason = score_pair(actor_profile(conn, a), actor_profile(conn, b))
        stats.pairs_scored += 1
        record_decision(conn, actor_id=a, candidate_id=b, decision=decision, method="score", score=score,
                        features=features, reason=reason)
        if decision == "match":
            keep, drop = _survivor(conn, a, b)
            merge_actors(conn, keep, drop)
            stats.merged += 1
        elif decision == "unsure":
            stats.unsure += 1
        else:
            stats.no_match += 1


def resolve_mention_candidates(conn: psycopg.Connection, mention_id: int, stats: ResolveStats) -> None:
    m = mention_profile(conn, mention_id)
    outcomes: dict[int, tuple[str, float | None]] = {}
    for candidate in mention_candidates(conn, mention_id):
        current = current_decision(conn, mention_id=mention_id, candidate_id=candidate)
        if current and METHOD_RANK[current[2]] > METHOD_RANK["score"]:
            outcomes[candidate] = (current[1], current[3])
            continue
        decision, score, features, reason = score_pair(m, actor_profile(conn, candidate))
        stats.pairs_scored += 1
        record_decision(conn, mention_id=mention_id, candidate_id=candidate, decision=decision, method="score",
                        score=score, features=features, reason=reason)
        outcomes[candidate] = (decision, score)

    matches = [c for c, (d, _) in outcomes.items() if d == "match"]
    if len(matches) == 1:
        link_mention(conn, mention_id, matches[0], "score", outcomes[matches[0]][1])
        _retire_open_decisions(conn, mention_id)
        stats.linked += 1
    elif len(matches) > 1:
        for c in matches:          # two actors fit: that's for a person (or a merge) to settle
            record_decision(conn, mention_id=mention_id, candidate_id=c, decision="unsure", method="score",
                            score=outcomes[c][1], features={"several_matches": matches},
                            reason="Several candidates match")
        stats.unsure += 1
    elif all(d == "no_match" for d, _ in outcomes.values()):
        create_actor_for_mention(conn, mention_id)
        stats.new_actors += 1
    else:
        stats.unsure += 1


def resolve_all(conn: psycopg.Connection) -> ResolveStats:
    """Duplicate actors first (so mentions meet the merged result), then unresolved
    mentions, then the relationships that became possible."""
    stats = ResolveStats()
    resolve_actor_pairs(conn, stats)
    for (mention_id,) in conn.execute("SELECT id FROM mentions WHERE actor_id IS NULL ORDER BY id").fetchall():
        resolve_mention_candidates(conn, mention_id, stats)
    stats.relationships = materialize_relationships(conn)
    return stats


# Human review 

REVIEW_COLUMNS = [
    "decision_id", "your_decision", "score", "reason",
    "left_kind", "left_id", "left_name", "left_websites", "left_countries", "left_funds", "left_description",
    "right_id", "right_name", "right_websites", "right_countries", "right_funds", "right_description",
    "notes",
]


def _side(prefix: str, p: Profile) -> dict[str, Any]:
    return {f"{prefix}_id": p.id, f"{prefix}_name": p.display, f"{prefix}_websites": " ".join(sorted(p.domains)),
            f"{prefix}_countries": ", ".join(sorted(p.countries)), f"{prefix}_funds": ", ".join(sorted(p.funds)),
            f"{prefix}_description": (p.descriptions[0] if p.descriptions else "")}


def review_rows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """The review queue with both sides spelled out, best scores first."""
    rows = []
    for decision_id, mention_id, actor_id, candidate_id, score, reason in conn.execute(
            """SELECT id, mention_id, actor_id, candidate_actor_id, score, reason FROM match_decisions
               WHERE is_current AND decision = 'unsure' ORDER BY score DESC NULLS LAST, id""").fetchall():
        left = mention_profile(conn, mention_id) if mention_id is not None else actor_profile(conn, actor_id)
        row = {"decision_id": decision_id, "your_decision": "", "score": score, "reason": reason,
               "left_kind": left.kind, "notes": ""}
        row |= _side("left", left) | _side("right", actor_profile(conn, candidate_id))
        rows.append(row)
    return rows


def export_review(conn: psycopg.Connection, path: Path) -> int:
    rows = review_rows(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def finish_rejected_mention(conn: psycopg.Connection, mention_id: int) -> bool:
    """When every candidate of a mention was rejected, it becomes a new actor. Returns True if it did.

    The new actor would meet the same candidates as actor pairs, so a person's
    or the judge's no_match is carried over to them (a score is just recomputed)."""
    still_open = conn.execute(
        """SELECT count(*) FROM match_decisions WHERE is_current AND mention_id = %s
           AND decision <> 'no_match'""", (mention_id,)).fetchone()[0]
    if still_open:
        return False
    new_id = create_actor_for_mention(conn, mention_id)
    for rejected, method, reason, decided_by in conn.execute(
            """SELECT candidate_actor_id, method, reason, decided_by FROM match_decisions
               WHERE is_current AND mention_id = %s AND method IN ('human', 'llm')""", (mention_id,)).fetchall():
        a, b = sorted((new_id, canonical(conn, rejected)))
        if a != b:
            record_decision(conn, actor_id=a, candidate_id=b, decision="no_match", method=method, score=None,
                            features={"from_mention": mention_id}, reason=reason, decided_by=decided_by)
    return True


def apply_decision(conn: psycopg.Connection, decision_id: int, decision: str, decided_by: str,
                   notes: str = "") -> str:
    """Record a person's decision on a review pair and carry it out. Returns what happened."""
    row = conn.execute(
        """SELECT mention_id, actor_id, candidate_actor_id, score, features, is_current, decision
           FROM match_decisions WHERE id = %s""", (decision_id,)).fetchone()
    if row is None:
        return "unknown decision id"
    mention_id, actor_id, candidate_id, score, features, is_current, old = row
    if not is_current or old != "unsure":
        return "already decided"
    reason = f"Reviewed by {decided_by}" + (f": {notes}" if notes else "")
    record_decision(conn, mention_id=mention_id, actor_id=actor_id, candidate_id=candidate_id, decision=decision,
                    method="human", score=score, features=features or {}, reason=reason, decided_by=decided_by)
    candidate = canonical(conn, candidate_id)

    if mention_id is not None:
        if conn.execute("SELECT actor_id FROM mentions WHERE id = %s", (mention_id,)).fetchone()[0] is not None:
            return "mention already resolved"
        if decision == "match":
            link_mention(conn, mention_id, candidate, "human", score)
            _retire_open_decisions(conn, mention_id)
            return "linked"
        if decision == "no_match" and finish_rejected_mention(conn, mention_id):
            return "new actor"
        return "recorded"

    if decision == "match":
        a = canonical(conn, actor_id)
        if a == candidate:
            return "already merged"
        keep, drop = _survivor(conn, a, candidate)
        merge_actors(conn, keep, drop)
        return "merged"
    return "recorded"


def apply_review(conn: psycopg.Connection, path: Path, decided_by: str) -> dict[str, int]:
    """Apply every row of a review CSV that has 'match' or 'no_match' in your_decision."""
    counts: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            decision = (row.get("your_decision") or "").strip().lower().replace(" ", "_").replace("-", "_")
            if decision in ("", "unsure", "skip"):
                continue
            if decision not in ("match", "no_match"):
                counts[f"ignored ({decision})"] = counts.get(f"ignored ({decision})", 0) + 1
                continue
            outcome = apply_decision(conn, int(row["decision_id"]), decision, decided_by, row.get("notes") or "")
            counts[outcome] = counts.get(outcome, 0) + 1
    counts["relationships"] = materialize_relationships(conn)
    return counts
