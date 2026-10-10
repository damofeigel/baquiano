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
