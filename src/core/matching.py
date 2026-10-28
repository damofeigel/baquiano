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


# Names that are not company names

# Sector tags and card badges a portfolio page shows next to (or instead of) a name.
# 17 Sigma's cards are logos with a tag, so a crawl read "AI & SaaS" as the name.
CATEGORY_WORDS = {
    "ai", "saas", "tech", "fintech", "foodtech", "agtech", "agrotech", "agrifood", "biotech", "healthtech",
    "medtech", "edtech", "hrtech", "proptech", "insurtech", "legaltech", "cleantech", "climatetech", "deeptech",
    "web3", "crypto", "blockchain", "energy", "mobility", "logistics", "marketplace", "b2b", "b2c", "iot",
    "exit", "exited", "acquired", "ipo", "portfolio", "startup", "startups", "other", "others", "all", "and",
}
GENERIC_SITES = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "github.com",
    "medium.com", "google.com", "sites.google.com", "linktr.ee", "angel.co", "wellfound.com", "crunchbase.com",
}


def looks_like_category(name: str, sector: str | None = None) -> bool:
    """True for a tag or badge recorded as a name: "AI & SaaS", "Exit FinTech", "Exited",
    or a name identical to the record's own sector."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    if not words:
        return False
    if sector and name.strip().casefold() == sector.strip().casefold():
        return True
    return all(word in CATEGORY_WORDS for word in words)


def name_from_website(url: str | None) -> str | None:
    """A usable name from a company's own domain: agribodytech.com -> "Agribodytech"."""
    domain = website_domain(url)
    if not domain or domain in GENERIC_SITES or any(domain.endswith("." + g) for g in GENERIC_SITES):
        return None
    label = domain.split(".")[0]
    return label.capitalize() if len(label) >= 3 else None


# Countries 

_COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u s": "United States", "u s a": "United States",
    "united states of america": "United States", "eeuu": "United States", "ee uu": "United States",
    "estados unidos": "United States", "uk": "United Kingdom", "u k": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom", "reino unido": "United Kingdom",
    "brasil": "Brazil", "méxico": "Mexico", "mexico": "Mexico", "perú": "Peru", "españa": "Spain",
    "argentine republic": "Argentina", "republica argentina": "Argentina", "república argentina": "Argentina",
    "korea": "South Korea", "republic of korea": "South Korea",
}


def normalize_country(value: str | None) -> str | None:
    """One spelling per country ("USA", "EE.UU." -> "United States"); others as given, trimmed."""
    if not value or not value.strip():
        return None
    key = re.sub(r"[.\s]+", " ", value.strip().lower()).strip()
    return _COUNTRY_ALIASES.get(key) or _COUNTRY_ALIASES.get(key.replace(" ", "")) or value.strip()


#  Funds

def site_label(site: str) -> str:
    """The brand part of a fund's site: "theyieldlablatam.com" -> "theyieldlablatam"."""
    return site.lower().removeprefix("www.").split(".")[0]


def sister_of(site: str, others: list[str]) -> str | None:
    """The main site when SITE is a regional sister of it (theyieldlablatam.com -> theyieldlab.com):
    its brand label starts with the other's, which has 6+ characters."""
    label = site_label(site)
    parents = [o for o in others if o != site and len(site_label(o)) >= 6
               and label != site_label(o) and label.startswith(site_label(o))]
    return min(parents, key=lambda o: len(site_label(o))) if parents else None
