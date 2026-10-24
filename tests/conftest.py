"""Shared fixtures.

Database tests (marker 'db') need TEST_DATABASE_URL: an admin connection, since
they create and drop their own databases. E.g. with a throwaway container:
    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -p 127.0.0.1:55433:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/postgres pytest -m db
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

ADMIN_URL = os.getenv("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parent.parent / "src" / "db" / "migrations"
TEMPLATE = "baquiano_test_template"


def _url_for(db: str) -> str:
    base, _, _ = ADMIN_URL.rpartition("/")
    return f"{base}/{db}"


@pytest.fixture(scope="session")
def template_db():
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEMPLATE)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEMPLATE)))
    with psycopg.connect(_url_for(TEMPLATE), autocommit=True) as conn:
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(migration.read_text(encoding="utf-8"))
    yield TEMPLATE
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEMPLATE)))


@pytest.fixture
def conn(template_db):
    import psycopg
    from psycopg import sql
    name = f"baquiano_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template_db)))
    connection = psycopg.connect(_url_for(name))
    yield connection
    connection.close()
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
