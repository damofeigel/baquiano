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
# Shared platforms: a link to one of these doesn't identify a company
GENERIC_SITES = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "github.com",
    "medium.com", "google.com", "sites.google.com", "wixsite.com", "notion.site", "linktr.ee", "angel.co",
    "wellfound.com", "crunchbase.com",
}


def is_shared_site(domain: str | None) -> bool:
    """linkedin.com, ar.linkedin.com, acme.medium.com..."""
    return bool(domain) and any(domain == g or domain.endswith("." + g) for g in GENERIC_SITES)


def company_domain(url: str | None) -> str | None:
    """The website's domain when it can identify a company, else None (no URL, or a shared platform)."""
    domain = website_domain(url)
    return None if is_shared_site(domain) else domain


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
    domain = company_domain(url)
    if not domain:
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


# What a regional site adds to its main site's name (theyieldlab + latam)
REGION_SUFFIXES = {
    "latam", "latinamerica", "la", "asia", "apac", "eu", "europe", "emea", "us", "usa", "na", "africa",
    "mena", "ar", "argentina", "br", "brasil", "brazil", "mx", "mexico", "cl", "chile", "co", "colombia",
    "uk", "global", "intl", "international",
}


def sister_of(site: str, others: list[str]) -> str | None:
    """The main site when SITE is a regional sister of it (theyieldlablatam.com -> theyieldlab.com):
    its brand label is another site's label plus a region, with an optional '-' between.
    Any other shared start is a different fund (argentinatech.com is not argentina.gob.ar)."""
    label = site_label(site)
    for other in others:
        parent = site_label(other)
        if other != site and len(parent) >= 4 and label.startswith(parent):
            if label[len(parent):].lstrip("-") in REGION_SUFFIXES:
                return other
    return None
