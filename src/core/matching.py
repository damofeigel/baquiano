"""Matching startup names and websites (standard library only).

Shared by the portfolio agent's recorder (merging within a run) and
scripts/merge_portfolios.py (merging across funds), so both follow the same
rules. Kept free of the agent's dependencies so the scripts run without them.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit


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


_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)


def web_url(url: str | None) -> str | None:
    """A URL a browser can open, or None when the value is no website.

    "www.kigui.io" -> "https://www.kigui.io", "//acme.com" -> "https://acme.com",
    "http:puna.bio" -> "http://puna.bio". Backslashes count as "/" (browsers read
    https://acme.com\\@foo.ar as acme.com) and spaces around the host are dropped
    ("neworgans.com.ar /es"). None for other schemes (tel:, mailto:, whatsapp:,
    ftp://), a bare email address (hola@gmail.com), a host with spaces inside
    ("acme .com") or nothing at all.
    """
    if not url:
        return None
    url = url.strip().replace("\\", "/")
    if url.startswith("//"):
        url = "https:" + url
    match = _SCHEME.match(url)
    if match and url[match.end():].startswith("//"):
        scheme, rest = match.group(1), url[match.end() + 2:]
        if scheme.lower() not in ("http", "https"):
            return None
    elif match and match.group(1).lower() in ("http", "https"):
        scheme, rest = match.group(1), url[match.end():].lstrip("/")      # "http:puna.bio"
    elif match and "." not in match.group(1):
        return None                                                        # tel:, mailto:, whatsapp:
    else:
        scheme, rest = "https", url                                        # "acme.com", "acme.com:8080"
        if "@" in re.split(r"[/?#]", rest, maxsplit=1)[0]:
            return None                                                    # an email address
    cut = re.search(r"[/?#]", rest)
    authority, tail = (rest[:cut.start()], rest[cut.start():]) if cut else (rest, "")
    authority = re.sub(r"\s*:\s*", ":", authority.strip())   # "neworgans.com.ar :8443"
    if not authority or re.search(r"\s", authority):
        return None                                                        # "not a url", "acme .com"
    return f"{scheme}://{authority}{tail}"


def website_domain(url: str | None) -> str | None:
    """The host of a website, lowercase, without www., a port, a user:password or a final
    dot: https://www.Puna.bio:443/es -> puna.bio. None when there is no website (see
    web_url). A scheme counts only at the start: puna.com.ar/?next=https://x.com -> puna.com.ar.
    The database has a copy in SQL (migrations/005_website_domain.sql): change both."""
    full = web_url(url)
    if not full:
        return None
    try:
        host = urlsplit(full).hostname or ""
    except ValueError:                                     # "https://[broken"
        return None
    host = host.rstrip(".").removeprefix("www.")
    return host if host and not re.search(r"[\s\[\]@:]", host) else None   # no IPv6 literal either


# Names that are not company names

# Sector tags and card badges a portfolio page shows next to (or instead of) a name.
# 17 Sigma's cards are logos with a tag, so a crawl read "AI & SaaS" as the name.
# TAG_WORDS are never a company's whole name; ORDINARY_WORDS can be ("Mobility",
# "Crypto Energy"), so a name made only of them is never dropped on their account.
TAG_WORDS = {
    "ai", "saas", "tech", "fintech", "foodtech", "agtech", "agrotech", "agrifood", "biotech", "healthtech",
    "medtech", "edtech", "hrtech", "proptech", "insurtech", "legaltech", "cleantech", "climatetech", "deeptech",
    "web3", "blockchain", "b2b", "b2c", "iot", "exit", "exited", "acquired", "ipo", "portfolio", "startup",
    "startups",
}
ORDINARY_WORDS = {"crypto", "energy", "mobility", "logistics", "marketplace", "other", "others", "all", "and"}
CATEGORY_WORDS = TAG_WORDS | ORDINARY_WORDS
# Shared platforms: a link to one of these doesn't identify a company
GENERIC_SITES = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "github.com",
    "medium.com", "google.com", "sites.google.com", "wixsite.com", "notion.site", "linktr.ee", "angel.co",
    "wellfound.com", "crunchbase.com",
    # Mail providers: "hola@gmail.com" read as a website names the provider, not the company
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com", "icloud.com",
    "proton.me", "protonmail.com",
}


def is_shared_site(domain: str | None) -> bool:
    """linkedin.com, ar.linkedin.com, acme.medium.com..."""
    return bool(domain) and any(domain == g or domain.endswith("." + g) for g in GENERIC_SITES)


def company_domain(url: str | None) -> str | None:
    """The website's domain when it can identify a company, else None (no URL, or a shared platform)."""
    domain = website_domain(url)
    return None if is_shared_site(domain) else domain


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def looks_like_category(name: str, sector: str | None = None) -> bool:
    """True for a name that may be a tag or badge: "AI & SaaS", "Exited", "Mobility", or a
    name identical to the record's own sector ("Health" / "Health": a tag card read as both).
    That may also be a real name the crawl copied into the sector ("Kilimo" / "Kilimo"),
    which the startup's website can confirm (website_confirms_name)."""
    words = _words(name)
    if not words:
        return False
    if sector and name.strip().casefold() == sector.strip().casefold():
        return True
    return all(word in CATEGORY_WORDS for word in words)


def surely_category(name: str) -> bool:
    """A category beyond doubt: category words with at least one tag word ("Exit FinTech",
    "AI & SaaS"). "Mobility", "Crypto Energy" or a name equal to its sector may be a
    company's real name."""
    words = _words(name)
    return bool(words) and all(w in CATEGORY_WORDS for w in words) and any(w in TAG_WORDS for w in words)


# Second levels under a country code that are part of the suffix: puna.com.ar, acme.co.uk
_SECOND_LEVELS = {"com", "co", "org", "net", "gob", "gov", "edu", "ac", "mil", "int", "nom", "ai", "io"}


def registrable_domain(domain: str | None) -> str | None:
    """The part of a domain its owner registered: app.foo.com -> foo.com, en.puna.com.ar -> puna.com.ar."""
    if not domain:
        return None
    parts = domain.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVELS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def name_from_website(url: str | None, not_from: set[str] | None = None) -> str | None:
    """A usable name from a company's own domain: agribodytech.com -> "Agribodytech",
    app.foo.com -> "Foo". None for a shared platform or a site in NOT_FROM (registrable
    domains, e.g. the fund's own site: a tag card linking there names the fund, not a company)."""
    domain = registrable_domain(company_domain(url))
    if not domain or domain in (not_from or set()):
        return None
    label = domain.split(".")[0]
    return label.capitalize() if len(label) >= 3 else None


def website_confirms_name(name: str, website_name: str | None) -> bool:
    """The startup's own domain spells NAME ("Logistics" at logistics.io) or contains it
    ("Kilimo" at kilimoapp.com, at least 4 characters): the name is real, not a tag.
    A tag word never counts ("FinTech" at fintechlatam.com is still a tag)."""
    if not website_name or surely_category(name):
        return False
    key, label = dedupe_key(name), dedupe_key(website_name)
    return key == label or (len(key) >= 4 and key in label)


# Countries 

# Keys as _country_key makes them: lowercase, no accents, no dots
_COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u s": "United States", "u s a": "United States",
    "united states of america": "United States", "eeuu": "United States", "ee uu": "United States",
    "estados unidos": "United States", "uk": "United Kingdom", "u k": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom", "reino unido": "United Kingdom",
    "brasil": "Brazil", "mexico": "Mexico", "peru": "Peru", "espana": "Spain",
    "argentine republic": "Argentina", "republica argentina": "Argentina",
    "korea": "South Korea", "republic of korea": "South Korea",
}
_LOWER_WORDS = {"and", "of", "the", "y", "de", "del", "da", "do"}


def _country_key(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[.\s]+", " ", plain.lower()).strip()


def normalize_country(value: str | None) -> str | None:
    """One spelling per country: "USA", "EE.UU." -> "United States"; "ARGENTINA",
    "argentina" -> "Argentina". Countries not in the alias list get their words
    capitalized ("EL SALVADOR" -> "El Salvador"), accents kept."""
    if not value or not value.strip():
        return None
    key = _country_key(value)
    if alias := _COUNTRY_ALIASES.get(key) or _COUNTRY_ALIASES.get(key.replace(" ", "")):
        return alias
    words = re.sub(r"\s+", " ", value.strip()).lower().split(" ")
    return " ".join(w if i and w in _LOWER_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


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
