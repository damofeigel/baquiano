"""Tests for result recording, context trimming and page-text budgeting.

These use the real Agents SDK types but never launch Chromium or call a model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.core.browser import BrowserConfig, extract_page_content
from src.custom_agents.portfolio_agent import agent as agent_module
from src.custom_agents.portfolio_agent.context import TRIMMED_NOTE, trim_old_page_outputs
from src.custom_agents.portfolio_agent.models import StartupInfo
from src.custom_agents.portfolio_agent.recorder import PortfolioRecorder


# --- PortfolioRecorder -------------------------------------------------------

def test_recorder_dedupes_by_name_and_merges_missing_fields():
    rec = PortfolioRecorder()
    rec.add_companies([StartupInfo(name="Acme Bio", website="https://acme.bio")])
    added, merged = rec.add_companies([
        StartupInfo(name="ACME bio", website="https://other.example", sector="biotech"),
    ])

    assert (added, merged) == (0, 1)
    [company] = rec.companies
    assert company.website == "https://acme.bio"  # existing value kept
    assert company.sector == "biotech"            # empty field filled


def test_recorder_dedupes_by_website_domain():
    rec = PortfolioRecorder()
    rec.add_companies([StartupInfo(name="Acme", website="https://www.acme.com/")])
    rec.add_companies([StartupInfo(name="Acme Inc.", website="acme.com")])

    assert len(rec.companies) == 1


def test_recorder_builds_result_with_fallbacks():
    rec = PortfolioRecorder()
    rec.add_companies([StartupInfo(name="Acme")])

    result = rec.build_result("https://fund.example")

    assert result.fund_name == "Unknown"
    assert result.source_url == "https://fund.example"
    assert result.startups[0].fund_name == "Unknown"
    assert "Fund name was not identified." in result.errors


def test_record_companies_tool_accepts_a_list_of_companies():
    rec = PortfolioRecorder()
    tool = next(t for t in rec.tools if t.name == "record_companies")

    assert tool.params_json_schema["properties"]["companies"]["type"] == "array"


def test_placeholder_strings_become_null():
    info = StartupInfo(
        name="Acme", website="not found", country="N/A",
        year_created="19", funding_amount_usd="$2,500,000",
    )

    assert info.website is None
    assert info.country is None
    assert info.year_created is None
    assert info.funding_amount_usd == 2_500_000.0


# --- Partial results on failure ----------------------------------------------

def test_run_keeps_recorded_companies_when_turn_limit_is_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    portfolio_agent = agent_module.PortfolioAgent()

    async def fake_run(agent, prompt, **kwargs):
        portfolio_agent.recorder.set_fund("Fund X", "https://fund.example/portfolio")
        portfolio_agent.recorder.add_companies([StartupInfo(name="Acme")])
        raise agent_module.MaxTurnsExceeded("Max turns (25) exceeded")

    monkeypatch.setattr(agent_module.Runner, "run", fake_run)
    try:
        result = asyncio.run(portfolio_agent.run("https://fund.example"))
    finally:
        portfolio_agent.close()

    assert result.fund_name == "Fund X"
    assert [s.name for s in result.startups] == ["Acme"]
    assert any("turn limit" in e for e in result.errors)
    [written] = tmp_path.glob("fund.example_*.json")
    assert json.loads(written.read_text())["startups"][0]["name"] == "Acme"


def test_run_reraises_when_nothing_was_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    portfolio_agent = agent_module.PortfolioAgent()

    async def fake_run(agent, prompt, **kwargs):
        raise agent_module.AgentsException("model broke")

    monkeypatch.setattr(agent_module.Runner, "run", fake_run)
    try:
        with pytest.raises(agent_module.AgentsException):
            asyncio.run(portfolio_agent.run("https://fund.example"))
    finally:
        portfolio_agent.close()


# --- Context trimming ---------------------------------------------------------

class _Data:
    def __init__(self, items):
        from agents.run_config import ModelInputData
        self.model_data = ModelInputData(input=items, instructions="sys")


def _call(call_id, name):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"}


def _output(call_id, text):
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def test_trims_all_but_the_most_recent_page_outputs():
    big = "x" * 2_000
    items = [
        {"role": "user", "content": "go"},
        _call("1", "navigate_to_url"), _output("1", big),
        _call("2", "record_companies"), _output("2", big),
        _call("3", "navigate_to_url"), _output("3", big),
        _call("4", "get_current_page_content"), _output("4", big),
    ]

    result = trim_old_page_outputs(keep_recent=2)(_Data(items))
    outputs = {i["call_id"]: i["output"] for i in result.input if i.get("type") == "function_call_output"}

    assert outputs["1"] == TRIMMED_NOTE
    assert outputs["2"] == big  # non-page tools are never trimmed
    assert outputs["3"] == big
    assert outputs["4"] == big
    assert items[2]["output"] == big  # original history is not mutated


# --- Page text budget ---------------------------------------------------------

def test_long_content_is_truncated_before_links():
    cfg = BrowserConfig()
    cfg.max_text_chars = 2_000
    cfg.max_links = 10
    paragraphs = "".join(f"<p>Paragraph {i} " + "lorem " * 20 + "</p>" for i in range(100))
    links = "".join(f'<a href="https://startup{i}.example">Startup {i}</a>' for i in range(10))
    html = f"<html><head><title>Fund</title></head><body>{paragraphs}{links}</body></html>"

    page = extract_page_content(html, base_url="https://fund.example", config=cfg)

    assert len(page.plain_text) <= cfg.max_text_chars
    assert "https://startup9.example" in page.plain_text
    assert "[truncated for length]" in page.plain_text


# --- Output files -------------------------------------------------------------

def test_each_run_writes_its_own_file(tmp_path):
    from src.custom_agents.portfolio_agent.models import FinderResult
    from src.custom_agents.portfolio_agent.output import write_result

    first = FinderResult(source_url="https://fund.example", fund_name="A", startups=[])
    second = FinderResult(source_url="https://fund.example", fund_name="B", startups=[])
    paths = [write_result(r, "https://www.fund.example/portfolio", tmp_path) for r in (first, second)]

    assert paths[0] != paths[1]  # same fund, same second: no overwrite
    assert paths[0].name.startswith("fund.example_")
    assert [json.loads(p.read_text())["fund_name"] for p in paths] == ["A", "B"]


def test_fund_slug_is_filesystem_safe():
    from src.custom_agents.portfolio_agent.output import fund_slug

    assert fund_slug("https://www.gridexponential.com/portfolio") == "gridexponential.com"
    assert fund_slug("gridexponential.com") == "gridexponential.com"
    assert fund_slug("http://user@Fund.Example:8080/x?y=1") == "fund.example"
    assert fund_slug("not a url") == "not-a-url"
