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


def guess_fund_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta and str(meta.get("content", "")).strip():
        return str(meta["content"]).strip()
    title = soup.find("title")
    if not title:
        return None
    parts = [p.strip() for p in re.split(r"\s[|\-–—·:]\s", title.get_text(strip=True))]
    parts = [
        p for p in parts
        if p and not _PORTFOLIO_RE.fullmatch(p) and p.lower() not in {"home", "inicio", "our portfolio", "nuestro portafolio"}
    ]
    return parts[0] if parts else None


# --- Card extraction ----------------------------------------------------------------

def _signature(el: Tag) -> tuple[str, str]:
    classes = sorted(re.sub(r"\d+", "", c) for c in el.get("class", []))
    return el.name, " ".join(classes)


def _clean_name(text: str) -> str | None:
    text = " ".join(text.split())
    if re.search(r"\.(png|jpe?g|svg|webp|gif)$", text, re.I):
        text = re.sub(r"\.(png|jpe?g|svg|webp|gif)$", "", text, flags=re.I).replace("-", " ").replace("_", " ")
    text = _LOGO_WORDS_RE.sub("", text)
    text = re.sub(r"^\s*(de|of)\s+", "", text, flags=re.I).strip(" -–|:·")
    if not text or len(text) > MAX_NAME_CHARS or text.lower() in _GENERIC_NAMES:
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
        if name := _clean_name(str(img.get("alt") or img.get("title") or "")):
            return name
    links = [card] if card.name == "a" else card.find_all("a")
    for a in links:
        for value in (a.get_text(" ", strip=True), a.get("aria-label"), a.get("title")):
            if isinstance(value, str) and (name := _clean_name(value)):
                return name
    for text in card.stripped_strings:
        if name := _clean_name(text):
            return name
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
    for tag in soup(_NAV_SKIP):
        tag.decompose()

    # Key by parent and child signature: category grids that share a layout
    # merge into one group, while unrelated lists elsewhere on the page do not.
    groups: dict[tuple, list[Tag]] = defaultdict(list)
    for parent in soup.find_all(True):
        children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 2:
            continue
        by_sig: dict[tuple[str, str], list[Tag]] = defaultdict(list)
        for child in children:
            by_sig[_signature(child)].append(child)
        for sig, siblings in by_sig.items():
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
            companies.append(StartupInfo(
                name=name,
                website=website,
                description=_card_description(card, name),
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
