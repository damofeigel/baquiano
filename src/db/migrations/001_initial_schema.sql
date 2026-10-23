-- Migration: 001_init.sql
-- Created: 2026-09-08
-- Description: Initial database setup
--
-- To apply: psql -d secyt -f migrations/001_init.sql

BEGIN;

-- Create a migrations tracking table so you know what's been applied
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Record this migration
INSERT INTO schema_migrations (filename) VALUES ('001_init.sql');

-- TODO: Metadata por ahora serian los atributos sin rigidez.
-- Quedaria pensar si merecen tablas unicas

CREATE TABLE actors (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    year_created INTEGER,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Un actor puede tener mas de un tipo. Por ejemplo una universidad
-- puede ser incubadora
CREATE TABLE actor_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);


CREATE TABLE actor_type_assignments (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    actor_type_id INTEGER NOT NULL REFERENCES actor_types(id),
    PRIMARY KEY (actor_id, actor_type_id)
);

-- CLASIFICATION
-- Para corporaciones, gobiernos, habilitadores
/*
CREATE TABLE actor_classifications (
    actor_id BIGINT PRIMARY KEY REFERENCES actors(id),
    ownership_type TEXT,
    organization_level TEXT,
    organization_size TEXT,
    year_created INTEGER,
    is_deep_tech_related BOOLEAN,
    notes TEXT
);
*/

CREATE TABLE ownership_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

INSERT INTO ownership_types (name) VALUES
('Public'),
('Private'),
('Mixed');

CREATE TABLE organization_levels (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

INSERT INTO organization_levels (name) VALUES
('National'),
('Provincial'),
('Municipal'),
('Global');

CREATE TABLE actor_classifications (
    actor_id BIGINT PRIMARY KEY REFERENCES actors(id),
    ownership_type_id INTEGER REFERENCES ownership_types(id),
    organization_level_id INTEGER REFERENCES organization_levels(id),
    organization_size TEXT,
    notes TEXT
);

-- AREAS
-- Nos permite tener una jerarquia
-- Biotechnology
--    Industrial Biotechnology
--    Medical Biotechnology
--    Agricultural Biotechnology

CREATE TABLE technological_areas (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    parent_id INTEGER REFERENCES technological_areas(id)
);

CREATE TABLE actor_technological_areas (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    technological_area_id INTEGER NOT NULL
        REFERENCES technological_areas(id),
    PRIMARY KEY (actor_id, technological_area_id)
);

-- Servicios / Capacidades
CREATE TABLE capabilities (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE actor_capabilities (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    capability_id INTEGER NOT NULL REFERENCES capabilities(id),
    description TEXT,
    PRIMARY KEY (actor_id, capability_id)
);

INSERT INTO capabilities (name) VALUES
('Qualified Human Resources'),
('Specialized Services'),
('Research and Development'),
('Asset Protection'),
('Scientific and Technological Infrastructure'),
('Commercialization of Results'),
('Incubation'),
('Acceleration'),
('New Company Creation');


-- TODO: Necesario?
CREATE TABLE sources (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT,
    source_type TEXT,
    publication_date DATE,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT,
    verified BOOLEAN,
    verified_by TEXT
);

CREATE TABLE actor_sources (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    source_id BIGINT NOT NULL REFERENCES sources(id),
    description TEXT,
    PRIMARY KEY (actor_id, source_id)
);

-- Relaciones entre actores
CREATE TABLE relationship_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

INSERT INTO relationship_types (name) VALUES
('Qualified Human Resources'),
('Specialized Services'),
('Research and Development'),
('Asset Protection'),
('Infrastructure Access'),
('Commercialization'),
('Incubation'),
('Acceleration'),
('New Company Creation');

CREATE TABLE relationships (
    id BIGSERIAL PRIMARY KEY,
    source_actor_id BIGINT NOT NULL REFERENCES actors(id),
    target_actor_id BIGINT NOT NULL REFERENCES actors(id),
    relationship_type_id INTEGER NOT NULL
        REFERENCES relationship_types(id),
    description TEXT,
    start_date DATE,
    end_date DATE,
    source_id BIGINT REFERENCES sources(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_actor_id <> target_actor_id)
);

CREATE TABLE locations (
    id BIGSERIAL PRIMARY KEY,
    country TEXT NOT NULL DEFAULT 'Argentina',
    province TEXT,
    municipality TEXT,
    address TEXT,
    latitude NUMERIC(9,6),
    longitude NUMERIC(9,6)
);

CREATE TABLE actor_locations (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    location_id BIGINT NOT NULL REFERENCES locations(id),
    location_type TEXT,
    PRIMARY KEY (actor_id, location_id)
);

-- Patentes

CREATE TABLE patents (
    id BIGSERIAL PRIMARY KEY,
    patent_number TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    filing_date DATE,
    grant_date DATE,
    publication_year INTEGER,
    country TEXT NOT NULL DEFAULT 'Argentina',
    status TEXT DEFAULT 'Granted',
    technological_area_id INTEGER REFERENCES technological_areas(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE actor_patents (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    patent_id BIGINT NOT NULL REFERENCES patents(id),
    PRIMARY KEY (actor_id, patent_id)
);

-- Financiamiento
CREATE TABLE funding_sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE actor_funding_sources (
    actor_id BIGINT NOT NULL REFERENCES actors(id),
    funding_source_id INTEGER NOT NULL
        REFERENCES funding_sources(id),
    description TEXT,
    PRIMARY KEY (actor_id, funding_source_id)
);

-- Rondas de financiamiento
CREATE TABLE funding_events (
    id BIGSERIAL PRIMARY KEY,
    recipient_actor_id BIGINT NOT NULL REFERENCES actors(id),
    round_type TEXT NOT NULL,
    amount NUMERIC(18,2),
    currency CHAR(3) DEFAULT 'USD',
    event_date DATE,
    valuation NUMERIC(18,2),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE funding_event_participants (
    funding_event_id BIGINT NOT NULL REFERENCES funding_events(id) ON DELETE CASCADE,
    investor_actor_id BIGINT NOT NULL REFERENCES actors(id),   -- Fondo, Ángel, Gobierno o Corporación que aporta
    role TEXT DEFAULT 'Investor',                               -- 'Lead Investor', 'Co-Investor', 'Grantor' (Subsidio)
    amount_contributed NUMERIC(18,2),                          -- Monto aportado por este inversor (opcional)
    PRIMARY KEY (funding_event_id, investor_actor_id)
);

-- The crawler uses ON CONFLICT (url) on sources
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_url_unique
    ON sources (url);

-- The crawler uses ON CONFLICT (name) on actors as a dedup key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_actors_name_unique
    ON actors (name);

CREATE INDEX IF NOT EXISTS idx_actor_type_assignments_type
    ON actor_type_assignments(actor_type_id);

CREATE INDEX IF NOT EXISTS idx_actor_tech_areas_area
    ON actor_technological_areas(technological_area_id);

CREATE INDEX IF NOT EXISTS idx_relationships_source
    ON relationships(source_actor_id);

CREATE INDEX IF NOT EXISTS idx_relationships_target
    ON relationships(target_actor_id);

CREATE INDEX IF NOT EXISTS idx_actor_locations_loc
    ON actor_locations(location_id);

CREATE INDEX IF NOT EXISTS idx_funding_events_recipient
    ON funding_events(recipient_actor_id);

CREATE INDEX IF NOT EXISTS idx_funding_participants_investor
    ON funding_event_participants(investor_actor_id);


COMMIT;