"""Matching startup names and websites (standard library only).

Shared by the portfolio agent's recorder (merging within a run) and
scripts/merge_portfolios.py (merging across funds), so both follow the same
rules. Kept free of the agent's dependencies so the scripts run without them.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


def same_company_name(a: str, b: str) -> bool:
    """Name variants of one company, e.g. from a fund's regional sites:
    "Heartbest" / "Heartbest Foods", "Botanical Solution" / "Botanical Solutions".

    The longer name must be the shorter plus whole extra words (no digits, so
    "Startup 1" / "Startup 10" and "Glaia" / "Glaia 2" stay apart), and the
    shorter needs 6+ characters so short names ("Agree" / "Agreena") stay apart.
    """
    (short_key, short), (long_key, long) = sorted(
        ((dedupe_key(a), a), (dedupe_key(b), b)), key=lambda pair: len(pair[0])
    )
    if not short_key:
        return False
    if short_key.rstrip("s") == long_key.rstrip("s") and short_key[-1:].isalpha():
        return True
    short_words, long_words = short.lower().split(), long.lower().split()
    extra = long_words[len(short_words):]
    return (
        len(short_key) >= 6
        and long_words[:len(short_words)] == short_words
        and bool(extra)
        and all(word.isalpha() for word in extra)
    )


def dedupe_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def website_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.") or None
