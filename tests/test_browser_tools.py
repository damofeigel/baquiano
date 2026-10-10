"""Unit tests for tools.py.

These tests stub the OpenAI Agents SDK decorator and BrowserManager, so they do not
launch Chromium, contact the network, or require a running vLLM server.

Run from this directory with:
    pytest -q
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import pytest

EXPECTED_TOOLS = {
    "navigate_to_url",
    "get_current_page_content",
    "get_page_content_from_url",
    "get_current_page_html",
    "search_current_page",
    "click_element",
    "scroll_page",
    "load_more",
}


class FakePageContent:
    plain_text = "# Fund Portfolio\n\n## Content\nExample Startup\n\n## Links\n- [Example Startup](https://startup.example)"

    def to_dict(self):
        return {
            "url": "https://fund.example/portfolio",
            "title": "Fund Portfolio",
            "headings": ["Portfolio"],
            "paragraphs": ["Example Startup"],
            "links": [
                {
                    "text": "Example Startup",
                    "href": "https://startup.example",
                }
            ],
            "plain_text": "# Fund Portfolio\nExample Startup",
        }


@pytest.fixture
def tools_module(monkeypatch):
    """Import tools.py against safe, deterministic stand-ins for its dependencies."""
    calls = []
    instances = []

    # The identity decorator lets tests invoke tool functions directly
    class AgentsStub(types.ModuleType):
        def __getattr__(self, name: str):
            if name == "set_tracing_disabled":
                return lambda *a, **kw: None
            return type(name, (), {})

    agents_stub = AgentsStub("agents")

    def function_tool(*, strict_mode=False):
        def decorate(func):
            func._test_strict_mode = strict_mode
            return func
        return decorate

    agents_stub.function_tool = function_tool

    browser_stub = types.ModuleType("src.core.browser")

    class FakeBrowserManager:
        html_content = "<html>" + ("x" * 15_000) + "</html>"
        fail_operation = None

        def __init__(self):
            self.calls = calls
            self.closed = False
            instances.append(self)

        def _record(self, method, *args, **kwargs):
            self.calls.append((method, args, kwargs))
            if self.fail_operation == method:
                raise RuntimeError(f"simulated failure in {method}")

        def go_to(self, url):
            self._record("go_to", url)
            return FakePageContent()

        def get_clean_content(self):
            self._record("get_clean_content")
            return FakePageContent()

        def get_clean_content_from_url(self, url):
            self._record("get_clean_content_from_url", url)
            return FakePageContent()

        def get_page_content(self):
            self._record("get_page_content")
            return self.html_content

        def search_page(self, query, snippet_radius=150, max_matches=8):
            self._record(
                "search_page",
                query,
                snippet_radius=snippet_radius,
                max_matches=max_matches,
            )
            return {
                "query": query,
                "found": True,
                "match_count": 1,
                "snippets": ["...portfolio includes Example Startup..."],
            }

        def click_element(self, selector, timeout_ms=5000):
            self._record("click_element", selector, timeout_ms=timeout_ms)
            return {"success": True, "selector": selector}

        def scroll_page(self, steps=3, scroll_pause_ms=600):
            self._record(
                "scroll_page", steps=steps, scroll_pause_ms=scroll_pause_ms
            )
            return {"steps_scrolled": steps}

        def load_more(self, load_more_selector, max_clicks=5, pause_ms=1000):
            self._record(
                "load_more",
                load_more_selector=load_more_selector,
                max_clicks=max_clicks,
                pause_ms=pause_ms,
            )
            return {"clicks": max_clicks, "stopped_reason": "completed"}

        def close(self):
            self.closed = True
            self.calls.append(("close", (), {}))

    browser_stub.BrowserManager = FakeBrowserManager

    # Ensure the module under test is freshly imported with the stubs above.
    monkeypatch.setitem(sys.modules, "agents", agents_stub)
    monkeypatch.setitem(sys.modules, "src.core.browser", browser_stub)
    sys.modules.pop("src.custom_agents.portfolio_agent.tools", None)

    module = importlib.import_module("src.custom_agents.portfolio_agent.tools")
    yield types.SimpleNamespace(
        module=module,
        calls=calls,
        instances=instances,
        BrowserManager=FakeBrowserManager,
    )

    sys.modules.pop("src.custom_agents.portfolio_agent.tools", None)


def run(coro):
    return asyncio.run(coro)


def tool_map(browser_tools):
    return {tool.__name__: tool for tool in browser_tools.tools}


def test_exposes_only_the_expected_browser_tools(tools_module):
    browser_tools = tools_module.module.BrowserTools()

    assert {tool.__name__ for tool in browser_tools.tools} == EXPECTED_TOOLS
    browser_tools.close()


def test_navigate_to_url_returns_structured_page_content(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(
            tool_map(browser_tools)["navigate_to_url"](
                "https://fund.example/portfolio"
            )
        )

        assert result == FakePageContent.plain_text
        assert tools_module.calls[0] == (
            "go_to",
            ("https://fund.example/portfolio",),
            {},
        )
    finally:
        browser_tools.close()


def test_get_page_content_from_url_calls_temporary_page_method(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(
            tool_map(browser_tools)["get_page_content_from_url"](
                "https://fund.example/companies"
            )
        )

        assert result == FakePageContent.plain_text
        assert tools_module.calls[0] == (
            "get_clean_content_from_url",
            ("https://fund.example/companies",),
            {},
        )
    finally:
        browser_tools.close()


def test_search_current_page_forwards_all_arguments(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(
            tool_map(browser_tools)["search_current_page"](
                "portfolio", snippet_radius=100, max_matches=3
            )
        )

        payload = json.loads(result)
        assert payload["found"] is True
        assert payload["query"] == "portfolio"
        assert tools_module.calls[0] == (
            "search_page",
            ("portfolio",),
            {"snippet_radius": 100, "max_matches": 3},
        )
    finally:
        browser_tools.close()


@pytest.mark.parametrize(
    ("tool_name", "args", "expected_call"),
    [
        (
            "click_element",
            {"selector": "button.load-more", "timeout_ms": 2500},
            (
                "click_element",
                ("button.load-more",),
                {"timeout_ms": 2500},
            ),
        ),
        (
            "scroll_page",
            {"steps": 4, "scroll_pause_ms": 200},
            (
                "scroll_page",
                (),
                {"steps": 4, "scroll_pause_ms": 200},
            ),
        ),
        (
            "load_more",
            {
                "load_more_selector": "button.more",
                "max_clicks": 2,
                "pause_ms": 100,
            },
            (
                "load_more",
                (),
                {
                    "load_more_selector": "button.more",
                    "max_clicks": 2,
                    "pause_ms": 100,
                },
            ),
        ),
    ],
)
def test_interaction_tools_forward_arguments(
    tools_module, tool_name, args, expected_call
):
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(tool_map(browser_tools)[tool_name](**args))
        assert json.loads(result)
        assert tools_module.calls[0] == expected_call
    finally:
        browser_tools.close()


def test_current_page_html_is_truncated_with_metadata(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(tool_map(browser_tools)["get_current_page_html"]())
        payload = json.loads(result)

        assert len(payload["html"]) == tools_module.module.MAX_HTML_CHARS
        assert payload["truncated"] is True
        assert payload["original_length"] == len(
            tools_module.BrowserManager.html_content
        )
    finally:
        browser_tools.close()


def test_browser_exception_is_returned_as_structured_error(tools_module):
    tools_module.BrowserManager.fail_operation = "go_to"
    browser_tools = tools_module.module.BrowserTools()
    try:
        result = run(
            tool_map(browser_tools)["navigate_to_url"](
                "https://fund.example/portfolio"
            )
        )
        payload = json.loads(result)

        assert payload["error"] == "RuntimeError: simulated failure in go_to"
        assert payload["operation"] == "go_to"
    finally:
        browser_tools.close()


def test_browser_manager_is_reused_between_tool_calls(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    try:
        run(tool_map(browser_tools)["get_current_page_content"]())
        run(
            tool_map(browser_tools)["navigate_to_url"](
                "https://fund.example/portfolio"
            )
        )

        assert len(tools_module.instances) == 1
    finally:
        browser_tools.close()


def test_close_closes_browser_and_subsequent_calls_return_error(tools_module):
    browser_tools = tools_module.module.BrowserTools()
    run(tool_map(browser_tools)["get_current_page_content"]())
    manager = tools_module.instances[0]

    browser_tools.close()

    assert manager.closed is True
    result = run(tool_map(browser_tools)["get_current_page_content"]())
    assert json.loads(result) == {"error": "BrowserTools is closed."}

    # Closing twice should be harmless.
    browser_tools.close()
