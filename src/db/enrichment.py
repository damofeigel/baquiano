"""Store enrichment results (scripts/enrich_startups.py) on the actors they describe.

A merged startup and an actor are matched by the company's own website domain, else
by name (any alias), but never across two different domains. The result goes under
actors.metadata -> 'enrichment', replacing an older one; the summary and the founding
year fill the actor's description and year_created only when those are empty. Actors
still waiting for review can share a name, so one startup may update several actors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.core.matching import company_domain


@dataclass
class EnrichmentStats:
    actors_updated: int = 0
    startups_matched: int = 0
    not_enriched: int = 0
    not_found: list[str] = field(default_factory=list)


def _actor_domains(conn: psycopg.Connection, actor_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT value FROM actor_identifiers WHERE actor_id = %s AND scheme = 'domain'", (actor_id,)
    ).fetchall()
    return {row[0] for row in rows}


def find_actors(conn: psycopg.Connection, startup: dict[str, Any]) -> list[int]:
    """The active actors a merged startup describes."""
    domain = company_domain(startup.get("website"))
    if domain:
        row = conn.execute(
            """SELECT a.id FROM actor_identifiers i JOIN active_actors a ON a.id = i.actor_id
               WHERE i.scheme = 'domain' AND i.value = %s""", (domain,)).fetchone()
        if row:
            return [row[0]]
    names = {name.casefold() for name in [startup["name"], *startup.get("aliases", [])]}
    rows = conn.execute(
        """SELECT DISTINCT a.id FROM actor_aliases al JOIN active_actors a ON a.id = al.actor_id
           WHERE lower(al.alias) = ANY(%s)""", (list(names),)).fetchall()
    found = [row[0] for row in rows]
    # "Nova" at nova.com is not "Nova" at nova.com.ar
    return [a for a in found if not (domain and (others := _actor_domains(conn, a)) and domain not in others)]


def apply_enrichment(conn: psycopg.Connection, startups: list[dict[str, Any]]) -> EnrichmentStats:
    stats = EnrichmentStats()
    for startup in startups:
        enrichment = startup.get("enrichment")
        if not enrichment:
            stats.not_enriched += 1
            continue
        actors = find_actors(conn, startup)
        if not actors:
            stats.not_found.append(startup["name"])
            continue
        stats.startups_matched += 1
        for actor_id in actors:
            conn.execute(
                """UPDATE actors SET metadata = jsonb_set(coalesce(metadata, '{}'::jsonb), '{enrichment}', %s),
                       description = coalesce(description, %s), year_created = coalesce(year_created, %s),
                       updated_at = NOW()
                   WHERE id = %s""",
                (Jsonb(enrichment), enrichment.get("summary"), enrichment.get("year_founded"), actor_id),
            )
            stats.actors_updated += 1
    return stats
