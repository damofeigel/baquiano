"""Load pipeline outputs into PostgreSQL as mentions and resolve the certain cases.

Every record a source produced becomes a MENTION (stored as found, never
merged in place). Resolution then links each mention to an ACTOR, but only
when that's certain:

  * same domain identifier as an existing actor -> linked (method 'identifier')
  * same normalized name, no shared identifier   -> left unresolved, with an
    'unsure' MATCH_DECISION for each candidate (review queue / step 3);
    'Kigüi' and 'Kigui' share a name key but are different companies
  * no candidate at all                           -> new actor ('new_actor')

Relationships a source states (a fund invests in its portfolio startups) are
stored between mentions and become RELATIONSHIPS once both are resolved.
Loading the same file again adds nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import psycopg

# Shared platforms: a link to one of these doesn't identify an actor
GENERIC_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com",
    "github.com", "medium.com", "google.com", "sites.google.com", "wixsite.com", "notion.site",
    "linktr.ee", "angel.co", "wellfound.com", "crunchbase.com",
}


@dataclass
class LoadStats:
    mentions_new: int = 0
    mentions_existing: int = 0
    linked: int = 0
    new_actors: int = 0
    unsure: int = 0
    relationships: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, other: LoadStats) -> None:
        for name in ("mentions_new", "mentions_existing", "linked", "new_actors", "unsure", "relationships"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.notes += other.notes


def raw_hash(raw: dict[str, Any]) -> str:
    return hashlib.md5(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# --- Rows ---------------------------------------------------------------------------

def upsert_source(conn: psycopg.Connection, url: str, title: str, source_type: str) -> int:
    row = conn.execute(
        """INSERT INTO sources (title, url, source_type) VALUES (%s, %s, %s)
           ON CONFLICT (url) DO UPDATE SET title = sources.title
           RETURNING id""",
        (title, url, source_type),
    ).fetchone()
    return row[0]


def insert_mention(
    conn: psycopg.Connection, *, source_id: int, run_ref: str, extractor: str, raw: dict[str, Any],
    name: str, website: str | None = None, country: str | None = None,
    description: str | None = None, actor_type_hint: str | None = None,
) -> tuple[int, bool]:
    """(mention id, True if new). The same record from the same source is stored once."""
    digest = raw_hash(raw)
    row = conn.execute(
        """INSERT INTO mentions (source_id, run_ref, extractor, raw, raw_hash, name, website,
                                 country, description, actor_type_hint)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (source_id, raw_hash) DO NOTHING
           RETURNING id""",
        (source_id, run_ref, extractor, json.dumps(raw, ensure_ascii=False), digest, name, website,
         country, description, actor_type_hint),
    ).fetchone()
    if row:
        return row[0], True
    existing = conn.execute(
        "SELECT id FROM mentions WHERE source_id = %s AND raw_hash = %s", (source_id, digest)
    ).fetchone()
    return existing[0], False


def _actor_type_id(conn: psycopg.Connection, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM actor_types WHERE name = %s", (name,)).fetchone()
    return row[0] if row else None


def _add_alias(conn: psycopg.Connection, actor_id: int, alias: str, source_id: int | None) -> None:
    conn.execute(
        """INSERT INTO actor_aliases (actor_id, alias, alias_type, source_id) VALUES (%s, %s, 'name', %s)
           ON CONFLICT (actor_id, lower(alias)) DO NOTHING""",
        (actor_id, alias, source_id),
    )


def _add_domain(conn: psycopg.Connection, actor_id: int, domain: str | None, source_id: int | None) -> None:
    if domain and domain not in GENERIC_DOMAINS:
        conn.execute(
            """INSERT INTO actor_identifiers (actor_id, scheme, value, source_id) VALUES (%s, 'domain', %s, %s)
               ON CONFLICT (scheme, value) DO NOTHING""",
            (actor_id, domain, source_id),
        )


def _add_type(conn: psycopg.Connection, actor_id: int, type_name: str | None) -> None:
    type_id = _actor_type_id(conn, type_name)
    if type_id is not None:
        conn.execute(
            """INSERT INTO actor_type_assignments (actor_id, actor_type_id) VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (actor_id, type_id),
        )


# --- Resolution -----------------------------------------------------------------------

def resolve_mention(conn: psycopg.Connection, mention_id: int) -> str:
    """Resolve one mention if it's certain. Returns 'linked', 'unsure', 'new_actor' or 'done'."""
    m = conn.execute(
        """SELECT name, name_key, domain, website, description, actor_type_hint, source_id, actor_id
           FROM mentions WHERE id = %s""",
        (mention_id,),
    ).fetchone()
    name, name_key, domain, website, description, type_hint, source_id, actor_id = m
    if actor_id is not None:
        return "done"

    # 1. A shared identifier is proof (merged actors resolve to what they were merged into)
    if domain and domain not in GENERIC_DOMAINS:
        row = conn.execute(
            """SELECT coalesce(a.merged_into_id, a.id) FROM actor_identifiers i
               JOIN actors a ON a.id = i.actor_id
               WHERE i.scheme = 'domain' AND i.value = %s""",
            (domain,),
        ).fetchone()
        if row:
            _link(conn, mention_id, row[0], "identifier", 1.0, name, website, description, type_hint, source_id)
            return "linked"

    # 2. Same normalized name without a shared identifier: a candidate, not proof
    candidates = []
    if name_key:
        candidates = conn.execute(
            """SELECT DISTINCT coalesce(a.merged_into_id, a.id) AS actor_id
               FROM actors a LEFT JOIN actor_aliases al ON al.actor_id = a.id
               WHERE a.status <> 'rejected' AND (a.name_key = %s OR al.alias_key = %s)""",
            (name_key, name_key),
        ).fetchall()
    if candidates:
        for (candidate_id,) in candidates:
            candidate_domains = [r[0] for r in conn.execute(
                "SELECT value FROM actor_identifiers WHERE actor_id = %s AND scheme = 'domain'", (candidate_id,)
            ).fetchall()]
            features = {"same_name_key": True, "mention_domain": domain, "candidate_domains": candidate_domains}
            conn.execute(
                """INSERT INTO match_decisions (mention_id, candidate_actor_id, decision, method, features, reason)
                   VALUES (%s, %s, 'unsure', 'rule', %s, %s)
                   ON CONFLICT (mention_id, candidate_actor_id) WHERE is_current AND mention_id IS NOT NULL
                   DO NOTHING""",
                (mention_id, candidate_id, json.dumps(features),
                 "Same normalized name but no shared identifier"),
            )
        return "unsure"

    # 3. Nothing like it yet: a new actor

    new_id = conn.execute(
        "INSERT INTO actors (name, description, website) VALUES (%s, %s, %s) RETURNING id",
        (name, description, website),
    ).fetchone()
    if new_id is None:
        return f"New actor found but insertion failed"
    new_id = new_id[0]
    _add_domain(conn, new_id, domain, source_id)
    _link(conn, mention_id, new_id, "new_actor", None, name, website, description, type_hint, source_id)
    return "new_actor"


def _link(conn: psycopg.Connection, mention_id: int, actor_id: int, method: str, score: float | None,
          name: str, website: str | None, description: str | None, type_hint: str | None,
          source_id: int | None) -> None:
    conn.execute(
        """UPDATE mentions SET actor_id = %s, resolution_method = %s, resolution_score = %s, resolved_at = NOW()
           WHERE id = %s""",
        (actor_id, method, score, mention_id),
    )
    _add_alias(conn, actor_id, name, source_id)
    _add_type(conn, actor_id, type_hint)
    # Fill gaps on the actor, never overwrite: conflicting values stay in the mentions
    conn.execute(
        """UPDATE actors SET website = coalesce(website, %s), description = coalesce(description, %s),
                             updated_at = NOW()
           WHERE id = %s""",
        (website, description, actor_id),
    )
    conn.execute(
        "INSERT INTO actor_sources (actor_id, source_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (actor_id, source_id),
    )


def resolve_pending(conn: psycopg.Connection) -> LoadStats:
    """Try to resolve every unresolved mention (new identifiers may have appeared)."""
    stats = LoadStats()
    for (mention_id,) in conn.execute("SELECT id FROM mentions WHERE actor_id IS NULL ORDER BY id").fetchall():
        outcome = resolve_mention(conn, mention_id)
        if outcome == "linked":
            stats.linked += 1
        elif outcome == "new_actor":
            stats.new_actors += 1
        elif outcome == "unsure":
            stats.unsure += 1
    return stats


def materialize_relationships(conn: psycopg.Connection) -> int:
    """Turn mention relations whose two mentions are resolved into relationships."""
    rows = conn.execute(
        """SELECT r.id, coalesce(sa.merged_into_id, sa.id), coalesce(ta.merged_into_id, ta.id),
                  r.relationship_type_id, r.method, r.confidence, r.evidence_quote, r.evidence_url, r.source_id
           FROM mention_relations r
           JOIN mentions sm ON sm.id = r.source_mention_id
           JOIN mentions tm ON tm.id = r.target_mention_id
           JOIN actors sa ON sa.id = sm.actor_id
           JOIN actors ta ON ta.id = tm.actor_id
           WHERE r.relationship_id IS NULL"""
    ).fetchall()
    created = 0
    for rel_id, source_actor, target_actor, type_id, method, confidence, quote, url, source_id in rows:
        if source_actor == target_actor:
            continue
        row = conn.execute(
            """INSERT INTO relationships (source_actor_id, target_actor_id, relationship_type_id, method,
                                          confidence, evidence_quote, evidence_url, source_id, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted')
               ON CONFLICT (source_actor_id, target_actor_id, relationship_type_id) DO NOTHING
               RETURNING id""",
            (source_actor, target_actor, type_id, method, confidence, quote, url, source_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT id FROM relationships
                   WHERE source_actor_id = %s AND target_actor_id = %s AND relationship_type_id = %s""",
                (source_actor, target_actor, type_id),
            ).fetchone()
        else:
            created += 1
        conn.execute("UPDATE mention_relations SET relationship_id = %s WHERE id = %s", (row[0], rel_id))
    return created


# --- Portfolio outputs ------------------------------------------------------------------

def load_portfolio(conn: psycopg.Connection, data: dict[str, Any], *, fund_site: str, fund_name: str,
                   run_ref: str) -> LoadStats:
    """Load one portfolio output (a FinderResult JSON): the fund, its startups and
    'Investment' relations from the fund to each startup."""
    stats = LoadStats()
    investment = conn.execute("SELECT id FROM relationship_types WHERE name = 'Investment'").fetchone()
    if investment is None:
        raise ValueError("No relationship found")
    investment = investment[0]
    fund_source_url = data.get("source_url") or f"https://{fund_site}"
    fund_source = upsert_source(conn, fund_source_url, f"{fund_name} portfolio", "portfolio")

    fund_website = f"https://{fund_site}" if "." in fund_site else None
    fund_raw = {"fund_name": fund_name, "fund_site": fund_site, "source_url": fund_source_url}
    fund_mention, created = insert_mention(
        conn, source_id=fund_source, run_ref=run_ref, extractor="portfolio_agent", raw=fund_raw,
        name=fund_name if fund_name != "Unknown" else fund_site, website=fund_website, actor_type_hint="Fund",
    )
    stats.mentions_new += created
    stats.mentions_existing += not created

    for startup in data.get("startups", []):
        if not startup.get("name"):
            continue
        page = startup.get("source_url") or fund_source_url
        source_id = fund_source if page == fund_source_url else upsert_source(
            conn, page, f"{fund_name} portfolio", "portfolio")
        mention_id, created = insert_mention(
            conn, source_id=source_id, run_ref=run_ref, extractor="portfolio_agent", raw=startup,
            name=startup["name"], website=startup.get("website"), country=startup.get("country"),
            description=startup.get("description"), actor_type_hint="Startup",
        )
        stats.mentions_new += created
        stats.mentions_existing += not created
        conn.execute(
            """INSERT INTO mention_relations (source_mention_id, target_mention_id, relationship_type_id,
                                              method, confidence, evidence_url, source_id)
               VALUES (%s, %s, %s, 'portfolio', 1.0, %s, %s)
               ON CONFLICT (source_mention_id, target_mention_id, relationship_type_id) DO NOTHING""",
            (fund_mention, mention_id, investment, page, source_id),
        )

    stats.add(resolve_pending(conn))
    stats.relationships += materialize_relationships(conn)
    return stats
