-- Migration: 003_entity_resolution.sql
-- Created: 2026-10-23
-- Description: Entity matching / resolution and provenance.
--
--   * Actors are no longer deduplicated by exact name. Records found by the
--     crawlers, Lens and enrichment are stored as MENTIONS and resolved to
--     canonical ACTORS; every decision is kept in MATCH_DECISIONS.
--   * Actors get ALIASES (other names) and IDENTIFIERS (domain, Lens
--     applicant name, CUIT...) used as matching keys.
--   * Merges are non-destructive: a duplicate actor points to the one it was
--     merged into (merged_into_id), so a wrong merge can be undone.
--   * Relationships record how they were found, with evidence and confidence.
--   * Defaults that silently invented facts (country 'Argentina', patent
--     status 'Granted') are removed.
--
-- Runs after 001_initial_schema.sql and 002_seed_data.sql (file-name order in
-- /docker-entrypoint-initdb.d) and works on top of the seeded rows.
--
-- To apply: psql -d <db> -f src/db/migrations/003_entity_resolution.sql

BEGIN;

INSERT INTO schema_migrations (filename) VALUES ('003_entity_resolution.sql');

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ============================================================
-- NAME NORMALIZATION
-- ============================================================
-- Two normalized forms of a name: no accents, lowercase, punctuation as
-- spaces, legal suffixes removed.
--   actor_name_norm: words kept, for similarity search (pg_trgm)
--     'CONSEJO NACIONAL DE INVESTIGACIONES CIENTIFICAS Y TECN CONICET' contains
--     'conicet': word_similarity() = 1.0 (plain similarity: 0.14)
--   actor_name_key: no spaces at all, for exact comparison
--     'PUNA BIO CORP' / 'Puna Bio' -> 'punabio';  'Hiamet S.A' -> 'hiamet'
-- An equal key is a CANDIDATE, not proof: 'Kigüi' (logistics) and 'Kigui'
-- (retail AI) are different companies with the same key. Only identifiers
-- (domain, CUIT...) are strong enough to match automatically.
-- unaccent() is only STABLE; calling it with an explicit dictionary inside an
-- IMMUTABLE function is the standard way to use it in generated columns.
CREATE OR REPLACE FUNCTION actor_name_norm(name TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT trim(regexp_replace(
        regexp_replace(
            lower(public.unaccent('public.unaccent'::regdictionary, coalesce(name, ''))),
            '[^a-z0-9]+', ' ', 'g'),
        '(\s+(s a s|s a u|s a|s r l|sas|sau|srl|sa|inc|corp|corporation|llc|ltd|ltda|limited|gmbh|spa|plc|pte|co|company))+\s*$',
        ''))
$$;

CREATE OR REPLACE FUNCTION actor_name_key(name TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT replace(actor_name_norm(name), ' ', '')
$$;

-- Website -> bare domain: 'https://www.Puna.bio/en/' -> 'puna.bio'
CREATE OR REPLACE FUNCTION website_domain(url TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT nullif(lower(regexp_replace(trim(url), '^([a-z]+://)?(www\.)?([^/:?#]+).*$', '\3', 'i')), '')
$$;


-- ============================================================
-- ACTORS: no exact-name dedup, matching key, non-destructive merges
-- ============================================================
-- Different companies share names ("Tomorrow", "Circular") and one company
-- appears under several ("PUNA BIO CORP", "Puna Bio"): resolution decides.
DROP INDEX IF EXISTS idx_actors_name_unique;

ALTER TABLE actors
    ADD COLUMN name_norm TEXT GENERATED ALWAYS AS (actor_name_norm(name)) STORED,
    ADD COLUMN name_key TEXT GENERATED ALWAYS AS (actor_name_key(name)) STORED,
    ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'merged', 'rejected')),
    ADD COLUMN merged_into_id BIGINT REFERENCES actors(id),
    ADD CONSTRAINT actors_merge_target CHECK (
        (status = 'merged') = (merged_into_id IS NOT NULL) AND merged_into_id IS DISTINCT FROM id
    );

CREATE INDEX idx_actors_name_key ON actors (name_key);
CREATE INDEX idx_actors_name_norm_trgm ON actors USING gin (name_norm gin_trgm_ops);
CREATE INDEX idx_actors_merged_into ON actors (merged_into_id) WHERE merged_into_id IS NOT NULL;

-- Actors to show and link to: duplicates that were merged away are hidden
CREATE VIEW active_actors AS
    SELECT * FROM actors WHERE status = 'active';


-- ============================================================
-- ALIASES: every name an actor is known by
-- ============================================================
CREATE TABLE actor_aliases (
    id BIGSERIAL PRIMARY KEY,
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    alias TEXT NOT NULL,
    alias_norm TEXT GENERATED ALWAYS AS (actor_name_norm(alias)) STORED,
    alias_key TEXT GENERATED ALWAYS AS (actor_name_key(alias)) STORED,
    alias_type TEXT NOT NULL DEFAULT 'name'
        CHECK (alias_type IN ('name', 'legal_name', 'acronym', 'former_name', 'spelling', 'translation')),
    source_id BIGINT REFERENCES sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique per actor ignoring case, NOT per key: 'BEEFLOW CORP' and 'Beeflow'
-- share a key but the legal name is worth keeping (Lens lists applicants by it)
CREATE UNIQUE INDEX idx_actor_aliases_unique ON actor_aliases (actor_id, lower(alias));
CREATE INDEX idx_actor_aliases_key ON actor_aliases (alias_key);
CREATE INDEX idx_actor_aliases_norm_trgm ON actor_aliases USING gin (alias_norm gin_trgm_ops);


-- ============================================================
-- IDENTIFIERS: strong matching keys
-- ============================================================
-- One value belongs to one actor (a domain, a CUIT). Two mentions with the
-- same identifier are the same actor.
CREATE TABLE actor_identifiers (
    id BIGSERIAL PRIMARY KEY,
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    scheme TEXT NOT NULL
        CHECK (scheme IN ('domain', 'lens_applicant', 'cuit', 'linkedin', 'crunchbase', 'wikidata', 'ror', 'other')),
    value TEXT NOT NULL CHECK (value = lower(trim(value)) AND value <> ''),
    source_id BIGINT REFERENCES sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scheme, value)
);

CREATE INDEX idx_actor_identifiers_actor ON actor_identifiers (actor_id);


-- ============================================================
-- MENTIONS: raw records as each source found them
-- ============================================================
-- Loaded from the JSON outputs (portfolio runs, Lens, enrichment, search)
-- without merging anything. Resolution fills actor_id; rerunning it with
-- better rules never loses data.
CREATE TABLE mentions (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT REFERENCES sources(id),
    run_ref TEXT,                           -- output file / run it came from
    extractor TEXT NOT NULL
        CHECK (extractor IN ('portfolio_prepass', 'portfolio_agent', 'merge', 'lens',
                             'enrichment', 'text_extraction', 'search', 'manual', 'seed')),
    raw JSONB NOT NULL,                     -- the record exactly as found
    raw_hash TEXT NOT NULL,                 -- md5 of raw: reloading a file adds nothing
    name TEXT NOT NULL,
    name_norm TEXT GENERATED ALWAYS AS (actor_name_norm(name)) STORED,
    name_key TEXT GENERATED ALWAYS AS (actor_name_key(name)) STORED,
    website TEXT,
    domain TEXT GENERATED ALWAYS AS (website_domain(website)) STORED,
    country TEXT,
    description TEXT,
    actor_type_hint TEXT,                   -- 'Startup', 'Fund', 'Academia'... if the source says
    actor_id BIGINT REFERENCES actors(id),  -- NULL until resolved
    resolution_method TEXT
        CHECK (resolution_method IN ('identifier', 'name_key', 'score', 'llm', 'agent', 'human', 'new_actor')),
    resolution_score REAL CHECK (resolution_score BETWEEN 0 AND 1),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULLS NOT DISTINCT: without it, mentions with no source_id could be loaded twice
    UNIQUE NULLS NOT DISTINCT (source_id, raw_hash)
);

CREATE INDEX idx_mentions_actor ON mentions (actor_id);
CREATE INDEX idx_mentions_unresolved ON mentions (id) WHERE actor_id IS NULL;
CREATE INDEX idx_mentions_domain ON mentions (domain) WHERE domain IS NOT NULL;
CREATE INDEX idx_mentions_name_key ON mentions (name_key);
CREATE INDEX idx_mentions_name_norm_trgm ON mentions USING gin (name_norm gin_trgm_ops);


-- ============================================================
-- MATCH DECISIONS: why a mention is (not) an actor, or two actors are one
-- ============================================================
-- Each candidate pair gets a decision with its method and evidence:
--   mention -> candidate actor   (resolving a new record), or
--   actor   -> candidate actor   (two existing actors found to be duplicates).
-- Newer decisions supersede older ones; the history stays.
CREATE TABLE match_decisions (
    id BIGSERIAL PRIMARY KEY,
    mention_id BIGINT REFERENCES mentions(id),
    actor_id BIGINT REFERENCES actors(id),
    candidate_actor_id BIGINT NOT NULL REFERENCES actors(id),
    decision TEXT NOT NULL CHECK (decision IN ('match', 'no_match', 'unsure')),
    method TEXT NOT NULL CHECK (method IN ('rule', 'score', 'llm', 'agent', 'human')),
    score REAL CHECK (score BETWEEN 0 AND 1),
    features JSONB NOT NULL DEFAULT '{}'::jsonb,   -- e.g. {"same_domain": true, "name_similarity": 0.82}
    reason TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{"url": ..., "quote": ...}]
    decided_by TEXT,                               -- model name, agent run or reviewer
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((mention_id IS NULL) <> (actor_id IS NULL)),
    CHECK (actor_id IS DISTINCT FROM candidate_actor_id)
);

-- At most one current decision per pair
CREATE UNIQUE INDEX idx_match_decisions_current_mention
    ON match_decisions (mention_id, candidate_actor_id) WHERE is_current AND mention_id IS NOT NULL;
CREATE UNIQUE INDEX idx_match_decisions_current_actor
    ON match_decisions (actor_id, candidate_actor_id) WHERE is_current AND actor_id IS NOT NULL;
CREATE INDEX idx_match_decisions_unsure ON match_decisions (decided_at) WHERE is_current AND decision = 'unsure';

-- Pairs waiting for a person to decide
CREATE VIEW match_review_queue AS
    SELECT d.id AS decision_id, d.method, d.score, d.reason,
           coalesce(m.name, a.name) AS record_name,
           coalesce(m.domain, website_domain(a.website)) AS record_domain,
           c.id AS candidate_actor_id, c.name AS candidate_name,
           website_domain(c.website) AS candidate_domain,
           d.features, d.evidence, d.decided_at
    FROM match_decisions d
    LEFT JOIN mentions m ON m.id = d.mention_id
    LEFT JOIN actors a ON a.id = d.actor_id
    JOIN actors c ON c.id = d.candidate_actor_id
    WHERE d.is_current AND d.decision = 'unsure';


-- ============================================================
-- RELATIONSHIPS: provenance, confidence, review status
-- ============================================================
-- Types for what the crawlers and Lens find; the existing nine (from the
-- capabilities) stay.
INSERT INTO relationship_types (name, description) VALUES
    ('Investment',   'Source invests in target (fund portfolio, funding round)'),
    ('Spin-off',     'Target is a spin-off of source (university, CONICET, company)'),
    ('Co-patenting', 'Source and target are co-applicants on a patent'),
    ('Partnership',  'Declared partnership or agreement between source and target'),
    ('Affiliation',  'Source belongs to target (institute of a university or CONICET)'),
    ('Grant',        'Source funds target through a grant or subsidy')
ON CONFLICT (name) DO NOTHING;

ALTER TABLE relationships
    ADD COLUMN method TEXT NOT NULL DEFAULT 'manual'
        CHECK (method IN ('manual', 'seed', 'portfolio', 'lens', 'text_extraction', 'agent', 'human')),
    ADD COLUMN confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    ADD COLUMN evidence_quote TEXT,
    ADD COLUMN evidence_url TEXT,
    ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('proposed', 'accepted', 'rejected')),
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Rows that existed before this migration came from the seed
UPDATE relationships SET method = 'seed';

-- Reloading the same evidence can't duplicate a relationship
CREATE UNIQUE INDEX idx_relationships_unique
    ON relationships (source_actor_id, target_actor_id, relationship_type_id);
CREATE INDEX idx_relationships_proposed ON relationships (created_at) WHERE status = 'proposed';


-- ============================================================
-- DEFAULTS THAT INVENTED FACTS
-- ============================================================
-- A foreign startup or a pending patent must not become Argentine / granted
-- just because a value was missing.
ALTER TABLE locations ALTER COLUMN country DROP DEFAULT;
ALTER TABLE patents ALTER COLUMN country DROP DEFAULT;
ALTER TABLE patents ALTER COLUMN status DROP DEFAULT;

-- Patents from Lens: stable id, family (same invention filed elsewhere), raw record
ALTER TABLE patents
    ADD COLUMN lens_id TEXT UNIQUE,
    ADD COLUMN jurisdiction TEXT,
    ADD COLUMN kind TEXT,
    ADD COLUMN family_id TEXT,
    ADD COLUMN raw JSONB;

CREATE INDEX idx_patents_family ON patents (family_id) WHERE family_id IS NOT NULL;

ALTER TABLE actor_patents
    ADD COLUMN role TEXT NOT NULL DEFAULT 'applicant'
        CHECK (role IN ('applicant', 'owner', 'inventor_affiliation')),
    ADD COLUMN source_id BIGINT REFERENCES sources(id);


-- ============================================================
-- BACKFILL from the rows already in the database (the seed)
-- ============================================================
-- Each actor's own name and metadata acronym become aliases...
INSERT INTO actor_aliases (actor_id, alias, alias_type)
SELECT id, name, 'name' FROM actors
ON CONFLICT (actor_id, lower(alias)) DO NOTHING;

INSERT INTO actor_aliases (actor_id, alias, alias_type)
SELECT id, metadata->>'acronym', 'acronym' FROM actors
WHERE coalesce(metadata->>'acronym', '') <> ''
ON CONFLICT (actor_id, lower(alias)) DO NOTHING;

-- ...and each website a domain identifier, except generic platforms that
-- many actors share
INSERT INTO actor_identifiers (actor_id, scheme, value)
SELECT id, 'domain', website_domain(website) FROM actors
WHERE website_domain(website) IS NOT NULL
  AND website_domain(website) NOT IN ('linkedin.com', 'facebook.com', 'instagram.com', 'x.com', 'twitter.com',
                                      'youtube.com', 'github.com', 'medium.com', 'google.com', 'sites.google.com')
ON CONFLICT (scheme, value) DO NOTHING;

COMMIT;
