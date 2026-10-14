# Page extraction tool for the portfolio agent
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from src.core import config

# Data model & Config

@dataclass
class PageContent:
    url: str
    title: str
    headings: list[str]          # h1–h4 text, in document order
    paragraphs: list[str]        # visible content text blocks (p, li)
    links: list[dict[str, str]]  # [{text, href}] – absolute URLs, deduplicated
    plain_text: str              # page in document order (one part of it), capped to max_text_chars
    part: int = 1                # which part of a long page plain_text holds
    total_parts: int = 1

    def __str__(self) -> str:
        return self.plain_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "headings": self.headings,
            "paragraphs": self.paragraphs,
            "links": self.links,
            "plain_text": self.plain_text,
            "part": self.part,
            "total_parts": self.total_parts,
        }


class BrowserConfig:
    def __init__(self):
        self.headless: bool = config.HEADLESS
        self.timeout_ms: int = config.TIMEOUT
        self.wait_after_load_ms: int = config.WAIT_AFTER_LOAD
        self.max_text_chars: int = config.MAX_TEXT_CHARS
        self.max_links: int = config.MAX_LINKS

    def to_dict(self) -> dict[str, Any]:
        return {
            "headless": self.headless,
            "timeout_ms": self.timeout_ms,
            "wait_after_load_ms": self.wait_after_load_ms,
            "max_text_chars": self.max_text_chars,
            "max_links": self.max_links,
        }

browser_config = BrowserConfig()

# Text of buttons that reveal more items in a listing (English and Spanish)
LOAD_MORE_RE = re.compile(
    r"\b(load|show|see|view)\s+more\b|\b(ver|cargar|mostrar)\s+m[aá]s\b", re.I
)


# Core extraction helpers

_BLOCK_TAGS = [
    "p", "div", "li", "ul", "ol", "section", "article", "header", "footer", "nav",
    "main", "aside", "table", "tr", "thead", "tbody", "dl", "dt", "dd", "figure",
    "figcaption", "blockquote", "form", "address",
]
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_SKIP_HREF = ("#", "javascript:", "mailto:", "tel:")


def _document_text(soup: BeautifulSoup, base_url: str) -> str:
    """Render the page as text in document order (mutates soup).

    Links stay inline as [text](url) and headings become "### text", so a
    card reads as one block: name, description, then its links.
    """
    for a in soup.find_all("a", href=True):
        raw = str(a.get("href") or "").strip()
        if not raw or raw.startswith(_SKIP_HREF):
            a.unwrap()
            continue
        href = (urljoin(base_url, raw) if base_url else raw).split("#")[0]
        if a.find(_BLOCK_TAGS + _HEADING_TAGS):
            # A whole card wrapped in one link: keep its content, link after it
            a.append(NavigableString(f"\n[Link]({href})\n"))
            a.unwrap()
            continue
        label = a.get_text(" ", strip=True)
        for attr in ("aria-label", "title"):
            value = a.get(attr)
            if not label and isinstance(value, str):
                label = value.strip()
        label = label.strip().strip("[]").strip() or href
        a.replace_with(NavigableString(f" [{label}]({href}) "))

    for h in soup.find_all(_HEADING_TAGS):
        text = h.get_text(" ", strip=True)
        h.replace_with(NavigableString(f"\n{'#' * int(h.name[1])} {text}\n" if text else "\n"))
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for el in soup.find_all(_BLOCK_TAGS):
        el.insert_before(NavigableString("\n"))
        el.insert_after(NavigableString("\n"))

    lines: list[str] = []
    for line in (soup.body or soup).get_text().splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def split_into_parts(text: str, size: int) -> list[str]:
    """Split text into parts of at most `size` chars, on line boundaries."""
    parts: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        line = line[:size]
        if current and length + len(line) + 1 > size:
            parts.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts or [""]


def extract_page_content(
    html: str,
    base_url: str = "",
    config: BrowserConfig | None = None,
    part: int = 1,
) -> PageContent:
    """Parse raw HTML and return clean, structured PageContent.

    plain_text renders the page in document order with links inline, so each
    card's name, description and URL stay together. (Grouping headings, text
    and links into separate lists made models pair them by position, and one
    skipped entry shifted every pair after it.) Pages longer than
    max_text_chars are split into parts; `part` selects which one.

    Preserves image alt text (essential for logo grids on VC portfolios) and
    resolves relative links against base_url.
    """
    cfg = config or browser_config
    soup = BeautifulSoup(html, "html.parser")

    # 1. Title (extract before decomposing head)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # 2. Preserve image alt / title attributes before decomposing
    # In VC portfolios, companies are often logo images where alt text is the only company name.
    for img in soup.find_all("img"):
        alt_value = (img.get("alt") or img.get("title") or "")
        if not isinstance(alt_value, str):
            continue
        alt = str(alt_value).strip()
        if alt:
            img.replace_with(f" [Logo: {alt}] ")
        else:
            img.decompose()

    # 3. Preserve SVG aria-label or title before decomposing
    for svg in soup.find_all("svg"):
        label_value = (svg.get("aria-label") or svg.get("title") or "")
        if not isinstance(label_value, str):
            continue
        label = str(label_value).strip()
        if label:
            svg.replace_with(f" [{label}] ")
        else:
            svg.decompose()

    # 4. Remove non-content tags
    for tag in soup(["script", "style", "noscript", "iframe", "head", "meta", "link"]):
        tag.decompose()

    # 5. Headings (h1–h4)
    headings: list[str] = []
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = h.get_text(" ", strip=True)
        if text and text not in headings:
            headings.append(text)

    # 6. Content blocks (<p>, <li>, <blockquote>, <article>)
    # Covers portfolios organized as bullet points or card containers
    paragraphs: list[str] = []
    seen_texts: set[str] = set()
    for el in soup.find_all(["p", "li", "article"]):
        text = el.get_text(" ", strip=True)
        # Avoid empty strings or single bullet glyphs
        if len(text.strip()) > 1 and text not in seen_texts:
            seen_texts.add(text)
            paragraphs.append(text)

    # 7. Links (resolve relative URLs, ignore anchors/scripts, deduplicate)
    links: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()
    for a in soup.find_all("a", href=True):
        raw_href_value = a.get("href")
        if not isinstance(raw_href_value, str):
            continue
        raw_href = str(raw_href_value).strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        resolved_href = urljoin(base_url, raw_href) if base_url else raw_href
        # Strip trailing URL fragments (e.g. #about, #top) to canonicalize
        clean_href = resolved_href.split("#")[0]
        if not clean_href:
            continue
        if clean_href in seen_hrefs:
            continue
        seen_hrefs.add(clean_href)

        aria_label = a.get("aria-label", "")
        if not isinstance(aria_label, str):
            aria_label = ""
        title_attr = a.get("title", "")
        if not isinstance(title_attr, str):
            title_attr = ""

        text = (
            a.get_text(" ", strip=True)
            or aria_label.strip()
            or title_attr.strip()
            or clean_href
        )
        links.append({"text": text, "href": clean_href})

        if len(links) >= cfg.max_links:
            break

    # 8. Plain text in document order, split into parts that fit the budget
    body = _document_text(soup, base_url)
    header = f"# {title}\n" if title else ""
    parts = split_into_parts(body, max(500, cfg.max_text_chars - len(header) - 200))
    total = len(parts)
    if not 1 <= part <= total:
        raise ValueError(f"This page has {total} part(s); part must be between 1 and {total}.")
    if not body:
        plain_text = ""  # nothing readable (e.g. about:blank); a title alone is not content
    else:
        plain_text = header
        if total > 1:
            plain_text += f"[Part {part} of {total}]\n"
        plain_text += parts[part - 1]
        if part < total:
            plain_text += (
                f"\n\n[Part {part} of {total}. The page continues: call "
                f"get_current_page_content with part={part + 1}.]"
            )

    return PageContent(
        url=base_url,
        title=title,
        headings=headings,
        paragraphs=paragraphs,
        links=links,
        plain_text=plain_text,
        part=part,
        total_parts=total,
    )


# ---------------------------------------------------------------------------
# BrowserManager
# ---------------------------------------------------------------------------

class BrowserManager:
    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or browser_config
        self.playwright = sync_playwright().start()
        try:
            self.browser: Browser = self.playwright.chromium.launch(headless=self.config.headless)
            self.context: BrowserContext = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
        except Exception:
            self.playwright.stop()
            raise
        self._active_page: Page | None = None

    def __enter__(self) -> BrowserManager:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    @property
    def page(self) -> Page:
        """Returns the current active page, creating one if needed."""
        if self._active_page is None or self._active_page.is_closed():
            self._active_page = self.new_page()
        return self._active_page

    def new_page(self) -> Page:
        page = self.context.new_page()
        self._active_page = page
        return page

    def close(self):
        try:
            self.context.close()
            self.browser.close()
            self.playwright.stop()
        except Exception:
            pass

    def wait_for_navigation(self, page: Page | None = None):
        """Wait for page load events and dynamic content to settle."""
        target_page = page or self.page
        try:
            target_page.wait_for_load_state("domcontentloaded", timeout=self.config.timeout_ms)
            try:
                target_page.wait_for_load_state("networkidle", timeout=1_000)
            except Exception:
                pass
        except Exception:
            pass
        if self.config.wait_after_load_ms > 0:
            target_page.wait_for_timeout(self.config.wait_after_load_ms)

    def go_to(self, page_or_url: Page | str, url: str | None = None) -> PageContent:
        """Navigate to a URL and wait for load.

        Supports both `go_to("https://...")` and `go_to(page, "https://...")`.
        Returns the parsed PageContent.
        """
        if isinstance(page_or_url, str):
            target_page = self.page
            nav_url = page_or_url
        else:
            target_page = page_or_url
            nav_url = url or ""

        if not nav_url:
            raise ValueError("URL must be provided to go_to")

        target_page.goto(nav_url, timeout=self.config.timeout_ms)
        self.wait_for_navigation(target_page)
        return self.get_clean_content(target_page)

    def get_page_content(self, page: Page | None = None) -> str:
        target_page = page or self.page
        return target_page.content()

    def get_clean_content(self, page: Page | None = None, part: int = 1) -> PageContent:
        target_page = page or self.page
        html = target_page.content()
        url = target_page.url
        return extract_page_content(html, base_url=url, config=self.config, part=part)

    def get_clean_content_from_url(self, url: str) -> PageContent:
        prev_page = self._active_page
        page = self.context.new_page()
        try:
            self.go_to(page, url)
            return self.get_clean_content(page)
        finally:
            page.close()
            self._active_page = prev_page

    def search_page(
        self,
        query: str,
        page: Page | None = None,
        snippet_radius: int = 150,
        max_matches: int = 8,
    ) -> dict[str, Any]:
        """Search visible page text for query and return surrounding context snippets."""
        target_page = page or self.page
        text = target_page.evaluate("() => document.body ? document.body.innerText : ''")
        if not text:
            return {"query": query, "found": False, "match_count": 0, "snippets": []}

        lower_text = text.lower()
        lower_query = query.lower()
        matches: list[str] = []
        start = 0

        while len(matches) < max_matches:
            idx = lower_text.find(lower_query, start)
            if idx == -1:
                break
            snip_start = max(0, idx - snippet_radius)
            snip_end = min(len(text), idx + len(query) + snippet_radius)
            snippet = " ".join(text[snip_start:snip_end].split())
            matches.append(f"...{snippet}...")
            start = idx + len(query)

        return {
            "query": query,
            "found": len(matches) > 0,
            "match_count": len(matches),
            "snippets": matches,
        }

    def click_element(
        self,
        selector: str,
        page: Page | None = None,
        timeout_ms: int = 5_000,
    ) -> dict[str, Any]:
        """Click an element specified by CSS selector or text."""
        target_page = page or self.page
        try:
            target_page.click(selector, timeout=timeout_ms)
            target_page.wait_for_timeout(500)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def scroll_page(
        self,
        page: Page | None = None,
        steps: int = 3,
        scroll_pause_ms: int = 600,
    ) -> dict[str, Any]:
        """Scroll progressively down the page to trigger lazy loading and infinite scroll."""
        target_page = page or self.page
        scrolled = 0
        for _ in range(steps):
            prev_height = target_page.evaluate("() => document.body.scrollHeight")
            target_page.evaluate("() => window.scrollBy(0, window.innerHeight * 1.5)")
            target_page.wait_for_timeout(scroll_pause_ms)
            scrolled += 1
            new_height = target_page.evaluate("() => document.body.scrollHeight")
            if new_height == prev_height:
                # Trigger bottom boundary
                target_page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                target_page.wait_for_timeout(scroll_pause_ms)
                break
        return {"steps_scrolled": scrolled}

    def load_more(
        self,
        load_more_selector: str,
        page: Page | None = None,
        max_clicks: int = 5,
        pause_ms: int = 1_000,
    ) -> dict[str, Any]:
        """Repeatedly click a 'Load More' button until exhausted or max_clicks reached."""
        target_page = page or self.page
        clicks = 0
        stopped_reason = "completed"

        for _ in range(max_clicks):
            try:
                locator = target_page.locator(load_more_selector).first
                if not locator.is_visible(timeout=1_500):
                    stopped_reason = "button_not_visible"
                    break
                if not locator.is_enabled(timeout=1_500):
                    stopped_reason = "button_disabled"
                    break
                locator.click(timeout=3_000)
                clicks += 1
                target_page.wait_for_timeout(pause_ms)
            except Exception as e:
                stopped_reason = f"stopped: {e}"
                break

        return {"clicks": clicks, "stopped_reason": stopped_reason}

    def expand_listing(
        self,
        page: Page | None = None,
        max_scrolls: int = 10,
        max_clicks: int = 5,
        pause_ms: int = 800,
    ) -> dict[str, Any]:
        """Scroll until the page stops growing, then click 'load more' buttons.

        Only <button>/role=button elements are clicked: "Ver más" links on
        individual cards usually open a company profile instead.
        """
        target_page = page or self.page
        height = "() => document.body ? document.body.scrollHeight : 0"

        def scroll_to_end() -> int:
            done = 0
            for _ in range(max_scrolls):
                before = target_page.evaluate(height)
                target_page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                target_page.wait_for_timeout(pause_ms)
                done += 1
                if target_page.evaluate(height) == before:
                    break
            return done

        scrolls = scroll_to_end()
        clicks = 0
        start_url = target_page.url
        for _ in range(max_clicks):
            button = target_page.get_by_role("button", name=LOAD_MORE_RE).first
            try:
                if button.count() == 0 or not button.is_visible():
                    break
                button.click(timeout=3_000)
            except Exception:
                break
            target_page.wait_for_timeout(pause_ms)
            if target_page.url != start_url:
                target_page.go_back(timeout=self.config.timeout_ms)
                break
            clicks += 1
            scrolls += scroll_to_end()
        return {"scrolls": scrolls, "clicks": clicks}
