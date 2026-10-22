"""Deterministic portfolio extraction from raw HTML (no LLM).

Most fund portfolios are a grid of repeated cards: logo, name, maybe a short
description and an external link. These helpers find that grid by looking for
sibling elements with the same tag and classes, and turn each card into a
StartupInfo. They are deliberately conservative: when unsure they return
nothing and the agent takes over.
"""

from __future__ import annotations

import re
from collections import defaultdict
from statistics import median
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from src.core.browser import remove_consent_dialogs

from .models import StartupInfo

MIN_CARDS = 4
MAX_NAME_CHARS = 60
MAX_CARD_TEXT = 500      # median card text above this looks like page sections, not cards
MAX_DESCRIPTION_CHARS = 300

PORTFOLIO_KEYWORDS = (
    "portfolio", "portafolio", "portfolio-companies", "companies", "empresas",
    "investments", "inversiones", "startups", "compañias", "companias", "compañías",
    "ventures", "participadas",
)
_PORTFOLIO_RE = re.compile("|".join(re.escape(k) for k in PORTFOLIO_KEYWORDS), re.I)

SOCIAL_DOMAINS = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "youtu.be", "medium.com", "crunchbase.com", "angel.co",
    "wellfound.com", "github.com", "wa.me", "t.me", "tiktok.com", "google.com",
    "apple.com", "goo.gl", "bit.ly",
)

_GENERIC_NAMES = {
    "logo", "image", "imagen", "website", "web", "sitio web", "visit", "visitar",
    "learn more", "read more", "see more", "view more", "more", "ver", "ver más",
    "ver mas", "leer más", "más info", "mas info", "más información", "details",
    "detalles", "link", "home", "inicio", "portfolio", "portafolio",
    # status badges shown on cards instead of the name ("Exited" -> agribodytech.com)
    "exited", "exit", "acquired", "new", "featured", "active", "realized", "ipo",
}
_ROLE_RE = re.compile(
    r"\b(ceo|cto|coo|cfo|founder|co-founder|partner|managing director|associate|"
    r"analyst|advisor|socio|socia|fundador|fundadora|director|directora|asesor)\b",
    re.I,
)
_LOGO_WORDS_RE = re.compile(r"\b(logo|logotipo|isologo|isotipo|image|imagen)\b", re.I)
_NAV_SKIP = ["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]


# --- URL helpers ----------------------------------------------------------------

def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _same_site(a: str, b: str) -> bool:
    ha, hb = _host(a), _host(b)
    return bool(ha) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def is_social_url(url: str) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS)


def _is_external_company_link(href: str, base_url: str) -> bool:
    return (
        href.startswith(("http://", "https://"))
        and not _same_site(href, base_url)
        and not is_social_url(href)
    )


# --- Page classification ----------------------------------------------------------

def looks_like_portfolio_url(url: str) -> bool:
    return bool(_PORTFOLIO_RE.search(urlparse(url).path))


def has_portfolio_heading(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return any(
        _PORTFOLIO_RE.search(h.get_text(" ", strip=True))
        for h in soup.find_all(["h1", "h2"])
    )


def find_portfolio_links(html: str, base_url: str) -> list[str]:
    """Same-site links that probably lead to the portfolio, best first."""
    soup = BeautifulSoup(html, "html.parser")
    current = base_url.split("#")[0].rstrip("/")
    scores: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, str(a["href"])).split("#")[0]
        if not href.startswith(("http://", "https://")) or not _same_site(href, base_url):
            continue
        if href.rstrip("/") == current:
            continue
        path = urlparse(href).path
        text = a.get_text(" ", strip=True)
        score = 0
        if _PORTFOLIO_RE.search(path):
            score += 2
        if _PORTFOLIO_RE.search(text) and len(text) <= 40:
            score += 3
        if re.search(r"portfolio|portafolio", path + " " + text, re.I):
            score += 2
        if score:
            # Deep paths are usually a single company profile, not the listing
            score -= max(0, path.strip("/").count("/") - 1)
            scores[href] = max(scores.get(href, 0), score)
    return [h for h, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0]


def _brand(url: str) -> str:
    """Main domain label: 'https://www.theyieldlab.com/x' -> 'theyieldlab'."""
    labels = _host(url).split(".")
    while len(labels) > 1 and len(labels[-1]) <= 3:  # drop com, ar, com.ar, co...
        labels.pop()
    return re.sub(r"[^a-z0-9]", "", labels[-1] if labels else "")


def find_sister_sites(html: str, base_url: str, limit: int = 6) -> list[str]:
    """Other sites of the same organisation, e.g. regional sites on their own domains.

    theyieldlab.com has no portfolio; it links to theyieldlablatam.com,
    theyieldlab.eu and theyieldlab.asia, which do. A sister site's domain
    contains the base site's brand name (at least 5 characters, to avoid
    matching unrelated sites).
    """
    brand = _brand(base_url)
    if len(brand) < 5:
        return []
    soup = BeautifulSoup(html, "html.parser")
    sites: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, str(a["href"]))
        if not href.startswith(("http://", "https://")) or _same_site(href, base_url):
            continue
        host = urlparse(href).netloc.lower()
        root = f"{urlparse(href).scheme}://{host}/"
        if brand in re.sub(r"[^a-z0-9.]", "", host.removeprefix("www.")) and root not in sites:
            sites.append(root)
    return sites[:limit]


# Countries a card may show next to the name ("BemAgro | Brazil")
_COUNTRIES = {
    "argentina", "brazil", "brasil", "chile", "colombia", "mexico", "méxico", "peru", "perú",
    "uruguay", "paraguay", "bolivia", "ecuador", "venezuela", "costa rica", "panama", "panamá",
    "guatemala", "honduras", "el salvador", "nicaragua", "dominican republic", "república dominicana",
    "puerto rico", "cuba", "usa", "us", "united states", "estados unidos", "canada", "spain", "españa",
    "portugal", "united kingdom", "uk", "france", "germany", "italy", "netherlands", "israel",
    "india", "singapore", "australia", "new zealand", "latam", "latin america",
    "ireland", "denmark", "sweden", "norway", "finland", "belgium", "switzerland", "austria",
    "poland", "indonesia", "malaysia", "myanmar", "thailand", "vietnam", "philippines",
    "japan", "china", "south korea", "kenya", "nigeria", "south africa", "egypt",
}
# Regions/labels that look like countries in tag lists but aren't one
_NOT_COUNTRIES = {"latam", "latin america"}


def country_from_tags(text: str | None) -> str | None:
    """Country in a tag-list description ("Crop Science Singapore Food Security").

    Only for short texts with no full stop (tags, not prose) naming exactly one country.
    """
    if not text or "." in text or len(text.split()) > 8:
        return None
    lowered = f" {text.lower()} "
    found = {c for c in _COUNTRIES - _NOT_COUNTRIES if len(c) > 2 and f" {c} " in lowered}
    if len(found) != 1:
        return None
    country = found.pop()
    start = lowered.index(f" {country} ") + 1
    return text[start - 1:start - 1 + len(country)]
_NAME_COUNTRY = re.compile(r"^(.+?)\s*[|·–—-]\s*([^|·–—-]+)$")


def split_country(name: str) -> tuple[str, str | None]:
    """'BemAgro | Brazil' -> ('BemAgro', 'Brazil'); other names are left alone."""
    match = _NAME_COUNTRY.match(name)
    if match and match.group(2).strip().lower() in _COUNTRIES:
        return match.group(1).strip(), match.group(2).strip()
    return name, None


def find_next_page_link(html: str, base_url: str, visited: set[str]) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.select('a[rel~="next"]') + [
        a for a in soup.find_all("a", href=True)
        if a.get_text(" ", strip=True).lower() in {"next", "siguiente", "›", "»", "next »", "siguiente »"}
        or any("next" in c.lower() for c in a.get("class", []))
    ]
    for a in candidates:
        if not a.get("href"):
            continue
        href = urljoin(base_url, str(a["href"])).split("#")[0]
        if _same_site(href, base_url) and href not in visited and href.rstrip("/") != base_url.rstrip("/"):
            return href
    return None


_TITLE_PAGE_WORDS = {"portfolio", "portafolio", "home", "inicio", "our", "nuestro", "nuestros", "nuestras"}


def fund_name_from_title(title: str) -> str | None:
    """'Newtopia Portfolio | 90 Latin American Startups' -> 'Newtopia'."""
    names = []
    for part in re.split(r"\s[|\-–—·:]\s", title.strip()):
        if _PORTFOLIO_RE.fullmatch(part):  # a whole part like "Portfolio" or "Startups"
            continue
        # Only strip page words: "Ventures" or "Investments" can be part of a fund's name
        words = [w for w in part.split() if w.lower() not in _TITLE_PAGE_WORDS]
        if words:
            names.append(" ".join(words))
    return names[0] if names else None


def guess_fund_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and str(meta.get("content", "")).strip():
        return str(meta["content"]).strip()
    title = soup.find("title")
    return fund_name_from_title(title.get_text(strip=True)) if title else None


# --- Card extraction ----------------------------------------------------------------

def _signature(el: Tag) -> tuple[str, str]:
    classes = sorted(re.sub(r"\d+", "", c) for c in el.get("class", []))
    return el.name, " ".join(classes)


_ALT_SEPARATOR = re.compile(r"\s[–—-]\s")
# "Lara (exit to Visma)", "Dexer (formely AgVoice)": the note moves to the description
_EXIT_NOTE = re.compile(
    r"^(.*?)\s*\(((?:exit|exited|acquired|formerly|formely|previously|ex)\b[^)]*)\)\s*$", re.IGNORECASE
)


def _split_alt(alt: str) -> tuple[str, str | None]:
    """'Aviva – Phygital neobank … | Newtopia VC portfolio' -> ('Aviva', 'Phygital neobank …')."""
    parts = _ALT_SEPARATOR.split(alt, maxsplit=1)
    # A name is short; "Kaffe Bueno utilises coffee by-products – such as…" is a sentence
    if len(parts) == 1 or len(parts[0].split()) > 4:
        return alt, None
    description = parts[1].split(" | ")[0].strip()
    return parts[0].strip(), description or None


def _class_set(el: Tag) -> frozenset[str]:
    return frozenset(re.sub(r"\d+", "", c) for c in el.get("class", []))


def _sibling_groups(children: list[Tag]) -> dict[tuple[str, str], list[Tag]]:
    """Group sibling elements that are the same kind of card.

    Siblings are grouped by tag and the classes most of them share, so per-card
    extras don't split one grid into many groups. (WordPress/Elementor cards
    carry classes like "elementor-filter-27" or a category tag on some cards
    only; grouping by the exact class list found 13 of 70 cards on such a page.)
    """
    by_tag: dict[str, list[Tag]] = defaultdict(list)
    for child in children:
        by_tag[child.name].append(child)

    groups: dict[tuple[str, str], list[Tag]] = defaultdict(list)
    for tag, members in by_tag.items():
        counts: dict[str, int] = defaultdict(int)
        for member in members:
            for cls in _class_set(member):
                counts[cls] += 1
        core = frozenset(c for c, n in counts.items() if n >= 0.6 * len(members))
        for member in members:
            # A card may lack an optional class (e.g. no filter category), so it
            # needs most of the shared classes, not all. A different kind of
            # element under the same parent shares few and keeps its own signature.
            shared = len(core & _class_set(member)) / len(core) if core else 1.0
            key = (tag, " ".join(sorted(core))) if shared >= 0.75 else _signature(member)
            groups[key].append(member)
    return groups


_LINK_LABEL = re.compile(
    r"^(visit|go to|ir a|visitar|ver sitio|see|read more|learn more|more about|leer m[aá]s|saber m[aá]s)\b",
    re.IGNORECASE,
)
_CONSENT = re.compile(r"\b(cookies?|consent|consentimiento)\b", re.IGNORECASE)
_LEADING_SYMBOLS = "↗↘→←↑↓›»«•·*▶►✓✔ "


def _clean_name(text: str) -> str | None:
    text = " ".join(text.split()).strip(_LEADING_SYMBOLS)  # "↗ Visit website"
    if re.search(r"\.(png|jpe?g|svg|webp|gif)$", text, re.I):
        text = re.sub(r"\.(png|jpe?g|svg|webp|gif)$", "", text, flags=re.I).replace("-", " ").replace("_", " ")
    text = _LOGO_WORDS_RE.sub("", text)
    text = re.sub(r"^\s*(de|of)\s+", "", text, flags=re.I).strip(" -–|:·")
    if not text or len(text) > MAX_NAME_CHARS or text.lower() in _GENERIC_NAMES:
        return None
    if _LINK_LABEL.match(text) or _CONSENT.search(text):  # "Visit website", cookie banners
        return None
    if text.lower().startswith(("http", "www.")):
        return None
    return text


def _card_name(card: Tag) -> str | None:
    named = card.find(["h1", "h2", "h3", "h4", "h5", "h6"]) or card.find(
        class_=re.compile(r"(^|[-_])(name|title|nombre)([-_]|$)", re.I)
    )
    if named and (name := _clean_name(named.get_text(" ", strip=True))):
        return name
    imgs = [card] if card.name == "img" else card.find_all("img")
    for img in imgs:
        alt, _ = _split_alt(str(img.get("alt") or img.get("title") or ""))
        if name := _clean_name(alt):
            return name
    links = [card] if card.name == "a" else card.find_all("a")
    for a in links:
        for value in (a.get_text(" ", strip=True), a.get("aria-label"), a.get("title")):
            if isinstance(value, str) and (name := _clean_name(value)):
                return name
    for text in card.stripped_strings:
        if name := _clean_name(text):
            return name
    return _name_from_slug(card)


_SLUG_SKIP = {"portfolio", "portafolio", "companies", "empresas", "startups", "about", "contact", "index"}


def _name_from_slug(card: Tag) -> str | None:
    """Last resort for cards with no name text: the detail link's slug.

    theyieldlab.eu cards are a logo whose alt is a description plus a "Read
    more about this company" link to /portfolio-items/apisprotect/. If the
    description starts with words matching the slug, keep that spelling
    ("ApisProtect"); otherwise title-case the slug ("Pure Ocean Algae").
    """
    links = [card] if card.name == "a" else card.find_all("a", href=True)
    for a in links:
        segments = [seg for seg in urlparse(str(a.get("href", ""))).path.split("/") if seg]
        if not segments:
            continue
        slug = segments[-1].lower()
        if slug in _SLUG_SKIP or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
            continue
        key = slug.replace("-", "")
        texts = [str(img.get("alt") or "") for img in card.find_all("img")] + list(card.stripped_strings)
        for text in texts:
            words = text.split()
            for n in range(1, min(5, len(words)) + 1):
                candidate = " ".join(words[:n]).strip(".,:;")
                if re.sub(r"[^a-z0-9]", "", candidate.lower()) == key:
                    return candidate
        return slug.replace("-", " ").title()
    return None


def _card_website(card: Tag, base_url: str) -> str | None:
    links = [card] if card.name == "a" else card.find_all("a", href=True)
    for a in links:
        href = urljoin(base_url, str(a.get("href", "")))
        if _is_external_company_link(href, base_url):
            return href
    return None


def _card_description(card: Tag, name: str) -> str | None:
    texts = [
        t for t in (p.get_text(" ", strip=True) for p in card.find_all(["p", "div", "span"]))
        if t and t != name and len(t) > len(name) + 20
    ]
    if not texts:
        # Some grids put the description only in the logo's alt text:
        # "Name – description", or a whole sentence about the company
        for img in ([card] if card.name == "img" else card.find_all("img")):
            alt = " ".join(str(img.get("alt") or "").split())
            alt_name, description = _split_alt(alt)
            if description and _clean_name(alt_name) == name:
                return description[:MAX_DESCRIPTION_CHARS]
            if len(alt) > len(name) + 30 and not description:
                return alt[:MAX_DESCRIPTION_CHARS]
        return None
    text = " ".join(min(texts, key=len).split())  # innermost block, not a wrapper
    return text[:MAX_DESCRIPTION_CHARS]


def _looks_like_team(cards: list[Tag]) -> bool:
    team_like = sum(
        1 for c in cards
        if _ROLE_RE.search(c.get_text(" ", strip=True))
        or c.find("a", href=re.compile(r"linkedin\.com/in/", re.I))
    )
    return team_like > len(cards) / 2


def extract_companies(html: str, base_url: str) -> list[StartupInfo]:
    """Return companies from the most portfolio-like repeated card group, or []."""
    soup = BeautifulSoup(html, "html.parser")
    remove_consent_dialogs(soup)
    for tag in soup(_NAV_SKIP):
        tag.decompose()

    # Key by parent and child signature: category grids that share a layout
    # merge into one group, while unrelated lists elsewhere on the page do not.
    groups: dict[tuple, list[Tag]] = defaultdict(list)
    for parent in soup.find_all(True):
        children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 2:
            continue
        for sig, siblings in _sibling_groups(children).items():
            if len(siblings) >= 2:
                groups[(_signature(parent), sig)].extend(siblings)

    best: list[StartupInfo] = []
    best_score = 0.0
    for cards in groups.values():
        if len(cards) < MIN_CARDS:
            continue
        if median(len(c.get_text(" ", strip=True)) for c in cards) > MAX_CARD_TEXT:
            continue
        if _looks_like_team(cards):
            continue

        companies: list[StartupInfo] = []
        seen: set[str] = set()
        external = logos = 0
        for card in cards:
            name = _card_name(card)
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            website = _card_website(card, base_url)
            external += website is not None
            logos += card.name == "img" or card.find("img") is not None
            name, country = split_country(name)
            description = _card_description(card, name)
            country = country or country_from_tags(description)
            
            if exit_match := _EXIT_NOTE.match(name):
                name = exit_match.group(1)
                note = re.sub(r"^formely\b", "formerly", exit_match.group(2).strip(), flags=re.IGNORECASE)
                note = note[0].upper() + note[1:] + "."
                description = f"{description.rstrip(' .')}. {note}" if description else note
            companies.append(StartupInfo(
                name=name,
                website=website,
                description=description,
                country=country,
                source_url=base_url,
            ))

        if len(companies) < MIN_CARDS or len(companies) / len(cards) < 0.6:
            continue
        # Company grids usually have logos and/or links to company sites
        score = len(companies) * (1 + external / len(companies) + 0.5 * logos / len(companies))
        if external == 0 and logos == 0:
            score *= 0.3
        if score > best_score:
            best, best_score = companies, score
    return best
