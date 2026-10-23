-- Migration: 004_mention_relations.sql
-- Created: 2026-10-23
-- Description: Relationships between mentions, and the actor types the
-- loader needs.
--
--   * A source states relationships between records it lists ("this fund
--     invests in these startups") before those records are resolved to
--     actors. MENTION_RELATIONS keeps them; scripts/load_db.py turns each into
--     a row of RELATIONSHIPS once both mentions are resolved.
--   * The six actor types were only inserted by the (optional) seed; the
--     loader assigns 'Startup' and 'Fund', so they must always exist.
--
-- To apply: psql -d <db> -f src/db/migrations/004_mention_relations.sql

BEGIN;

INSERT INTO schema_migrations (filename) VALUES ('004_mention_relations.sql');

INSERT INTO actor_types (name, description) VALUES
    ('Academia',    'Universidades, centros de investigación, institutos CONICET'),
    ('Startup',     'Empresas de base tecnológica / deep tech en etapa temprana'),
    ('Corporation', 'Empresas establecidas con actividad en deep tech'),
    ('Government',  'Organismos gubernamentales de CyT, ministerios, agencias'),
    ('Enabler',     'Incubadoras, aceleradoras, parques tecnológicos, UVTs'),
    ('Fund',        'Fondos de inversión, capital ángel, venture capital')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE mention_relations (
    id BIGSERIAL PRIMARY KEY,
    source_mention_id BIGINT NOT NULL REFERENCES mentions(id),
    target_mention_id BIGINT NOT NULL REFERENCES mentions(id),
    relationship_type_id INTEGER NOT NULL REFERENCES relationship_types(id),
    method TEXT NOT NULL
        CHECK (method IN ('portfolio', 'lens', 'text_extraction', 'agent', 'human')),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    evidence_quote TEXT,
    evidence_url TEXT,
    source_id BIGINT REFERENCES sources(id),
    relationship_id BIGINT REFERENCES relationships(id),  -- set once materialized
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_mention_id <> target_mention_id),
    UNIQUE (source_mention_id, target_mention_id, relationship_type_id)
);

CREATE INDEX idx_mention_relations_pending ON mention_relations (id) WHERE relationship_id IS NULL;

COMMIT;
