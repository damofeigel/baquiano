"""Database connection from DATABASE_URL (.env)."""

from __future__ import annotations

import psycopg

from src.core import config


def connect(url: str | None = None) -> psycopg.Connection:
    """A new connection. Not in autocommit: the caller commits or rolls back."""
    url = url or config.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env (see .env.example).")
    return psycopg.connect(url)
