"""The SQL website_domain() (migrations/005) must agree with src/core/matching.website_domain."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from src.core.matching import website_domain  # noqa: E402

pytestmark = pytest.mark.db

URLS = [
    "https://www.Puna.bio/en/", "puna.bio", "www.puna.com.ar/", "HTTPS://WWW.Puna.BIO./es",
    "empresa.com.ar/?next=https://x.com", "empresa.com.ar/go?u=http://x.com/a",
    "  neworgans.com.ar  ", "neworgans.com.ar.", "https://www.neworgans.com.ar./es",
    "https://acme.com:443/x", "acme.com:8080", "https://foo.ar:pw@evil.com", "https://user@Acme.com",
    "//acme.com/about", "http:puna.bio", "https:/glaia.com", "https://acme.com\\@foo.ar", "https://foo.ar\\@evil.com",
    "neworgans.com.ar /es", "https://neworgans.com.ar :8443/", "neworgans.com.ar. /x",
    "tel:+5491111111111", "mailto:hola@acme.com", "whatsapp:+5491111", "javascript:void(0)", "ftp://acme.com",
    "hola@gmail.com", "contacto@acme.com.ar/x", "/portfolio", "not a url", "acme .com",
    "https://[broken", "https://[website]", "https://[::1]/", "", "   ", None,
    "\xa0acme.com", "acme.com\xa0", "acme.com\n/es", "\tacme.com :\u00a08080/x", "acme.com\u2003",
    "acme\xa0.com", "\u3000https://www.acme.com\u3000", "acme.com\r\n",          # scraped spaces
]


def test_sql_and_python_read_websites_the_same_way(conn):
    sql = [conn.execute("SELECT website_domain(%s)", (url,)).fetchone()[0] for url in URLS]
    assert list(zip(URLS, sql)) == [(url, website_domain(url)) for url in URLS]


def test_mentions_get_the_new_domain(conn):
    source = conn.execute("INSERT INTO sources (url, title, source_type) VALUES ('https://f.example', 'f', 'portfolio') "
                          "RETURNING id").fetchone()[0]
    rows = conn.execute(
        """INSERT INTO mentions (source_id, run_ref, extractor, raw, raw_hash, name, website)
           SELECT %s, 'r', 'portfolio_agent', '{}', md5(w), 'x', w FROM unnest(%s::text[]) AS w RETURNING website, domain""",
        (source, ["https://foo.ar:pw@evil.com", "tel:+5491111", "hola@gmail.com"])).fetchall()
    assert dict(rows) == {"https://foo.ar:pw@evil.com": "evil.com", "tel:+5491111": None, "hola@gmail.com": None}
