"""Expose only BrowserManager browser actions as OpenAI Agents SDK tools.

Example:
    browser_tools = BrowserTools()
    agent = Agent(..., tools=browser_tools.tools)
    try:
        result = await Runner.run(agent, "Inspect https://fund.example/portfolio")
    finally:
        browser_tools.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse
from agents import function_tool

from src.core.browser import BrowserManager
from src.core.config import MAX_HTML_CHARS

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Browser methods whose output is page text the model can legitimately cite
_PAGE_METHODS = {"go_to", "get_clean_content", "get_clean_content_from_url", "get_page_content"}
_EVIDENCE_METHODS = _PAGE_METHODS | {"search_page"}


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def url_key(url: str) -> str:
    """Comparable form of a URL: no scheme, "www.", trailing slash or #fragment."""
    parts = urlparse(url if "://" in url else f"https://{url}")
    key = parts.netloc.lower().removeprefix("www.") + parts.path.rstrip("/")
    return f"{key}?{parts.query}" if parts.query else key


class Evidence:
    """Text the browser has actually returned during this run.

    Lets the record tools reject companies the model never saw on a page,
    e.g. invented placeholders like "Company 1".
    """

    MAX_CHARS = 500_000

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pages_read = 0
        self._chunks: list[str] = []
        self._size = 0
        self._opened: set[str] = set()
        self._attempted: set[str] = set()
        self.site_name: str | None = None   # first og:site_name seen (fund-name fallback)
        self.page_title: str | None = None  # first page title seen

    def add(self, text: str, is_page: bool, url: str | None = None) -> None:
        if is_page:
            self.pages_read += 1
            if url:
                self._opened.add(url_key(url))
        chunk = _normalize(text)
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > self.MAX_CHARS and len(self._chunks) > 1:
            self._size -= len(self._chunks.pop(0))

    def contains(self, text: str) -> bool:
        key = _normalize(text)
        return len(key) >= 2 and any(key in chunk for chunk in self._chunks)

    def mark_attempted(self, url: str) -> None:
        self._attempted.add(url_key(url))

    def opened(self, url: str) -> bool:
        """True if the page was read, or opening it was at least tried.

        Counting failed attempts keeps a dead link from blocking finish().
        """
        key = url_key(url)
        return key in self._opened or key in self._attempted


class BrowserTools:
    """Browser-only tools; Playwright is confined to one dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._browser: BrowserManager | None = None
        self._closed = False
        self.evidence = Evidence()
        self.tools = self._build_tools()
        logger.info("BrowserTools initialized (%d tools registered)", len(self.tools))

    def _get_browser(self) -> BrowserManager:
        # Always called on the dedicated Playwright thread.
        if self._browser is None:
            logger.info("Lazy-initializing BrowserManager on playwright thread")
            self._browser = BrowserManager()
            logger.info("BrowserManager ready")
        return self._browser

    @staticmethod
    def _json(value: Any) -> str:
        # Page content goes to the model as its compact markdown only; the full
        # dict repeats paragraphs and links and bypasses the max_text_chars cap.
        plain_text = getattr(value, "plain_text", None)
        if isinstance(plain_text, str):
            return plain_text
        if hasattr(value, "to_dict") and callable(value.to_dict):
            value = value.to_dict()
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

    def _invoke_sync(self, method: str, *args: Any, **kwargs: Any) -> str:
        if self._closed:
            logger.warning("Tool call '%s' rejected — BrowserTools is closed", method)
            return json.dumps({"error": "BrowserTools is closed."})
        logger.info("Tool call START: %s(args=%s, kwargs=%s)", method, args, kwargs)
        t0 = time.perf_counter()
        try:
            if method in ("go_to", "get_clean_content_from_url") and args:
                self.evidence.mark_attempted(str(args[0]))
            value = getattr(self._get_browser(), method)(*args, **kwargs)
            # Final URL after redirects: PageContent carries it; raw HTML comes from the current page
            page_url = getattr(value, "url", None)
            if method == "get_page_content":
                page_url = getattr(getattr(self._browser, "page", None), "url", None)
            elapsed = time.perf_counter() - t0
            if method == "get_page_content" and isinstance(value, str):
                truncated = len(value) > MAX_HTML_CHARS
                value = {
                    "html": value[:MAX_HTML_CHARS],
                    "truncated": truncated,
                    "original_length": len(value),
                }
                if truncated:
                    logger.info(
                        "Tool call OK: %s — truncated %d→%d chars (%.2fs)",
                        method, value["original_length"], MAX_HTML_CHARS, elapsed,
                    )
            result = self._json(value)
            if method in _PAGE_METHODS and (
                (isinstance(page_url, str) and page_url.startswith("about:")) or not result.strip()
            ):
                # Not a page read: the browser's blank start page, or a page with
                # no readable text. Counting it would switch off the "read a
                # page first" guards, and the model gets a clear next step.
                logger.info("Tool call OK: %s — no page content (%s)", method, page_url)
                if isinstance(page_url, str) and page_url.startswith("about:"):
                    message = "No page is open yet. Use navigate_to_url with the fund's website first."
                else:
                    message = "This page has no readable text. Try search_current_page, scroll_page or get_current_page_html."
                return json.dumps({"error": message, "operation": method})
            if method in _PAGE_METHODS:
                if not self.evidence.site_name and isinstance(getattr(value, "site_name", None), str):
                    self.evidence.site_name = value.site_name or None
                if not self.evidence.page_title and isinstance(getattr(value, "title", None), str):
                    self.evidence.page_title = value.title or None
            if method in _EVIDENCE_METHODS:
                self.evidence.add(
                    result, is_page=method in _PAGE_METHODS,
                    url=page_url if isinstance(page_url, str) else None,
                )
            logger.info("Tool call OK: %s — %d chars returned (%.2fs)", method, len(result), elapsed)
            logger.debug("Tool call RESULT: %s → %.500s", method, result)
            return result
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            err_str = str(exc)
            if "Target page, context or browser has been closed" in err_str or "Connection closed" in err_str:
                logger.warning(
                    "Tool call ERROR: %s — browser connection lost, will re-init (%.2fs): %s",
                    method, elapsed, exc,
                )
                self._browser = None
            else:
                logger.error(
                    "Tool call ERROR: %s — %s: %s (%.2fs)",
                    method, type(exc).__name__, exc, elapsed,
                    exc_info=True,
                )
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}", "operation": method},
                ensure_ascii=False,
            )

    async def _invoke(self, method: str, *args: Any, **kwargs: Any) -> str:
        if self._closed:
            logger.warning("Async invoke '%s' rejected — BrowserTools is closed", method)
            return json.dumps({"error": "BrowserTools is closed."})
        logger.debug("Dispatching '%s' to playwright executor thread", method)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: self._invoke_sync(method, *args, **kwargs),
        )

    async def run_with_browser(self, fn: Callable[[BrowserManager], T]) -> T:
        """Run fn(browser) on the Playwright thread and return its raw result.

        Used by non-agent code (the deterministic pre-pass) so it shares the
        agent's browser and leaves it on the page it finished on.
        """
        if self._closed:
            raise RuntimeError("BrowserTools is closed.")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(self._get_browser()))

    def _build_tools(self) -> list[Any]:
        @function_tool(strict_mode=False)
        async def navigate_to_url(url: str) -> str:
            """Navigate to a URL; return the page title, headings, content, and links."""
            return await self._invoke("go_to", url)

        @function_tool(strict_mode=False)
        async def get_current_page_content(part: int = 1) -> str:
            """Return the current page's text. Long pages come in parts: read part 2, 3...
            when the text ends with "The page continues"."""
            return await self._invoke("get_clean_content", part=part)

        @function_tool(strict_mode=False)
        async def get_page_content_from_url(url: str) -> str:
            """Fetch cleaned content from a URL using a temporary page."""
            return await self._invoke("get_clean_content_from_url", url)

        @function_tool(strict_mode=False)
        async def get_current_page_html() -> str:
            """Return the start of the raw HTML (truncated); use only if cleaned content is insufficient."""
            return await self._invoke("get_page_content")

        @function_tool(strict_mode=False)
        async def search_current_page(
            query: str, snippet_radius: int = 150, max_matches: int = 8
        ) -> str:
            """Search visible text on the current page and return contextual snippets."""
            return await self._invoke(
                "search_page", query,
                snippet_radius=snippet_radius, max_matches=max_matches,
            )

        @function_tool(strict_mode=False)
        async def click_element(selector: str, timeout_ms: int = 5000) -> str:
            """Click a page element using a Playwright selector, e.g. a tab or button."""
            return await self._invoke(
                "click_element", selector, timeout_ms=timeout_ms
            )

        @function_tool(strict_mode=False)
        async def scroll_page(steps: int = 3, scroll_pause_ms: int = 600) -> str:
            """Scroll down to trigger lazy-loaded or infinite-scroll content."""
            return await self._invoke(
                "scroll_page", steps=steps, scroll_pause_ms=scroll_pause_ms
            )

        @function_tool(strict_mode=False)
        async def load_more(
            load_more_selector: str, max_clicks: int = 5, pause_ms: int = 1000
        ) -> str:
            """Click a 'Load more' control repeatedly, up to max_clicks times."""
            return await self._invoke(
                "load_more", load_more_selector=load_more_selector,
                max_clicks=max_clicks, pause_ms=pause_ms,
            )

        return [
            navigate_to_url,
            get_current_page_content,
            get_page_content_from_url,
            get_current_page_html,
            search_current_page,
            click_element,
            scroll_page,
            load_more,
        ]

    def _close_browser_sync(self) -> None:
        if self._browser is not None:
            logger.info("Closing BrowserManager on playwright thread")
            self._browser.close()
            self._browser = None
            logger.info("BrowserManager closed")

    def close(self) -> None:
        """Close Playwright after the agent run finishes."""
        if self._closed:
            logger.debug("close() called but already closed — skipping")
            return
        self._closed = True
        logger.info("Shutting down BrowserTools")
        try:
            self._executor.submit(self._close_browser_sync).result()
        except Exception:
            logger.error("Error during browser shutdown", exc_info=True)
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)
            logger.info("BrowserTools shutdown complete")

    def __enter__(self) -> "BrowserTools":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
