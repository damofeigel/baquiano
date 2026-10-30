-- Migration: 005_website_domain.sql
-- Created: 2026-10-29
-- Description: website_domain() reads a website the way the Python code does
-- (src/core/matching.py: web_url and website_domain), so mentions.domain and
-- the domains merge_portfolios.py matches on agree.
--
--   * Only http(s) websites have a domain: tel:, mailto:, whatsapp:, ftp://,
--     a bare email address (hola@gmail.com) and broken values get none.
--   * The host drops a user:password ('https://foo.ar:pw@evil.com' is
--     evil.com), a port, a final dot and spaces around it; a backslash counts
--     as '/' (as browsers read it); '//acme.com' and 'http:puna.bio' work.
--   * mentions.domain (a stored generated column) is recomputed. Domain
--     identifiers already stored on actors are left as they are.
--
-- Change it together with the Python version: tests/test_db_website_domain.py
-- compares the two.
--
-- To apply: psql -d <db> -f src/db/migrations/005_website_domain.sql

BEGIN;

CREATE OR REPLACE FUNCTION website_domain(url TEXT) RETURNS TEXT
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    u TEXT;
    scheme TEXT;
    rest TEXT;
    authority TEXT;
    host TEXT;
BEGIN
    u := replace(regexp_replace(coalesce(url, ''), '^\s+|\s+$', '', 'g'), E'\\', '/');
    IF u = '' THEN
        RETURN NULL;
    END IF;
    IF u LIKE '//%' THEN
        u := 'https:' || u;                                  -- protocol-relative
    END IF;
    scheme := lower(substring(u FROM '^([A-Za-z][A-Za-z0-9+.-]*):'));
    IF scheme IS NOT NULL AND substr(u, length(scheme) + 2, 2) = '//' THEN
        IF scheme NOT IN ('http', 'https') THEN
            RETURN NULL;                                     -- ftp://, mailto://...
        END IF;
        rest := substr(u, length(scheme) + 4);
    ELSIF scheme IN ('http', 'https') THEN
        rest := regexp_replace(substr(u, length(scheme) + 2), '^/+', '');   -- 'http:puna.bio'
    ELSIF scheme IS NOT NULL AND position('.' IN scheme) = 0 THEN
        RETURN NULL;                                         -- tel:, mailto:, whatsapp:
    ELSE
        rest := u;                                           -- 'acme.com', 'acme.com:8080'
        IF position('@' IN substring(rest FROM '^[^/?#]*')) > 0 THEN
            RETURN NULL;                                     -- an email address
        END IF;
    END IF;

    authority := regexp_replace(btrim(substring(rest FROM '^[^/?#]*')), '\s*:\s*', ':', 'g');
    IF authority = '' OR authority ~ '\s' THEN
        RETURN NULL;                                         -- 'acme .com'
    END IF;
    host := regexp_replace(authority, '^.*@', '');           -- user:password
    IF host ~ '[\[\]]' THEN
        RETURN NULL;                                         -- 'https://[broken', IPv6 literals
    END IF;
    host := lower(split_part(host, ':', 1));                 -- the port
    host := regexp_replace(rtrim(host, '.'), '^www\.', '');
    RETURN nullif(host, '');
END
$$;

-- A stored generated column is only computed when its row is written
UPDATE mentions SET website = website WHERE website IS NOT NULL;

COMMIT;
