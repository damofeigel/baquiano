"""End-to-end pre-pass on a local fixture site with real Chromium (no internet).

The fixture has a homepage linking to a portfolio page that reveals more cards
via a "Ver más" button, plus a second page reached through a "Siguiente" link.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from src.core.browser import BrowserManager
from src.custom_agents.portfolio_agent.prepass import _prepass_sync

pytestmark = pytest.mark.browser

SITE = Path(__file__).parent / "fixtures" / "portfolio_site"


@pytest.fixture
def site_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def browser():
    try:
        manager = BrowserManager()
    except Exception as exc:
        pytest.skip(f"Chromium not available: {exc}")
    yield manager
    manager.close()


def test_prepass_follows_portfolio_link_load_more_and_pagination(browser, site_url):
    result = _prepass_sync(browser, site_url)

    assert result.fund_name_hint == "Fondo Andino"
    assert result.portfolio_url == f"{site_url}portafolio.html"
    names = [c.name for c in result.companies]
    assert len(names) == 12
    assert "Fueguina Energy" in names   # only appears after two "Ver más" clicks
    assert "Salta Solar" in names       # only on page 2
    assert result.companies[0].website == "https://andesbio.com"


def _set_page(browser, html):
    browser.page.set_content(f"<html><body>{html}</body></html>")


def test_load_more_clicks_through_a_popup_that_covers_it(browser):
    # sosv.com: a newsletter popup over the page made the click time out, and the list stopped at 10
    _set_page(browser, """
        <div id="list"><a href="https://a.example">A</a></div>
        <button onclick="this.previousElementSibling.insertAdjacentHTML('beforeend', '<a href=https://b.example>B</a>');
                         if (document.links.length >= 3) this.remove()">Load more</button>
        <div style="position:fixed;inset:0;background:white;z-index:99">Subscribe to our newsletter</div>""")
    result = browser.expand_listing(pause_ms=10)
    assert result["clicks"] == 2 and browser.page.locator("#list a").count() == 3
    assert result["limit_hit"] is False


def test_load_more_stops_when_a_button_loads_nothing(browser):
    _set_page(browser, '<a href="https://a.example">A</a><button>Show more</button>')
    assert browser.expand_listing(pause_ms=10)["clicks"] == 0


def test_click_limit_is_reported(browser):
    _set_page(browser, """<div id="l"></div><button onclick="document.getElementById('l').insertAdjacentHTML(
        'beforeend', '<a href=https://x.example>x</a>')">Load more</button>""")
    result = browser.expand_listing(max_clicks=2, pause_ms=10)
    assert result["clicks"] == 2 and result["limit_hit"] is True


def test_next_control_without_a_link_turns_the_page(browser):
    # 500.co: <a aria-label="Go to next page">Next</a> with no href, the list swapped by a script
    _set_page(browser, """<div id="t">page 1</div>
        <a aria-label="Go to next page" onclick="document.getElementById('t').textContent='page 2'">Next</a>""")
    assert browser.click_next_page(pause_ms=10) is True
    assert browser.page.inner_text("#t") == "page 2"


def test_no_next_control_or_one_that_changes_nothing(browser):
    _set_page(browser, "<p>one page</p>")
    assert browser.click_next_page(pause_ms=10) is False
    _set_page(browser, "<p>last page</p><a>Next</a>")
    assert browser.click_next_page(pause_ms=10) is False


def test_show_all_counts_as_a_load_button(browser):
    # kamayventures.com: "Show all 23 companies"
    _set_page(browser, """<div id="l"><a href="https://a.example">A</a></div>
        <button onclick="document.getElementById('l').insertAdjacentHTML('beforeend',
            '<a href=https://b.example>B</a>'); this.remove()">Show all 23 companies</button>""")
    assert browser.expand_listing(pause_ms=10)["clicks"] == 1
