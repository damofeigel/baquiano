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

def test_long_pages_are_split_into_parts_without_losing_anything():
    cfg = BrowserConfig()
    cfg.max_text_chars = 2_000
    paragraphs = "".join(f"<p>Paragraph {i} " + "lorem " * 20 + "</p>" for i in range(100))
    links = "".join(f'<a href="https://startup{i}.example">Startup {i}</a>' for i in range(10))
    html = f"<html><head><title>Fund</title></head><body>{paragraphs}{links}</body></html>"

    first = extract_page_content(html, base_url="https://fund.example", config=cfg)
    parts = [extract_page_content(html, base_url="https://fund.example", config=cfg, part=n)
             for n in range(1, first.total_parts + 1)]

    assert first.total_parts > 1
    assert all(len(p.plain_text) <= cfg.max_text_chars for p in parts)
    assert "call get_current_page_content with part=2" in first.plain_text
    assert "page continues" not in parts[-1].plain_text
    everything = "\n".join(p.plain_text for p in parts)
    assert all(f"Paragraph {i} " in everything for i in range(100))
    assert all(f"[Startup {i}](https://startup{i}.example)" in everything for i in range(10))


def test_part_out_of_range_is_a_clear_error():
    with pytest.raises(ValueError, match="has 1 part"):
        extract_page_content("<html><body><p>Hi there</p></body></html>", part=2)


# Shaped like the GridX portfolio: every card has a logo, a name, a description
# and usually a link. CASPR's link is LinkedIn, Werken Vac has none.
GRID_HTML = """<html><head><title>Portfolio</title></head><body>
<div class="grid">
  <div class="card"><img alt="Alkemio"><h3>Alkemio</h3><div>Rare earth separation</div>
    <a href="https://alkemio.tech">Visit Website</a></div>
  <div class="card"><img alt="CASPR"><h3>CASPR</h3><div>CRISPR diagnostics</div>
    <a href="https://www.linkedin.com/company/casprbio/">Visit Website</a></div>
  <div class="card"><img alt="Werken Vac"><h3>Werken Vac</h3><div>RNA vaccine for fish</div></div>
  <div class="card"><img alt="Zavia Bio"><h3>Zavia Bio</h3><div>Protein design platform</div>
    <a href="https://www.zaviabio.com">Visit Website</a></div>
</div>
<footer><a href="/contact-us">JOIN US</a></footer>
</body></html>"""


def test_cards_stay_together_in_document_order():
    text = extract_page_content(GRID_HTML, base_url="https://fund.example/portfolio").plain_text

    # Each card is one block: name, then its description, then its own link
    assert "### Werken Vac\nRNA vaccine for fish\n[Logo: Zavia Bio]\n### Zavia Bio" in text
    assert ("### Zavia Bio\nProtein design platform\n"
            "[Visit Website](https://www.zaviabio.com)") in text
    assert "### Alkemio\nRare earth separation\n[Visit Website](https://alkemio.tech)" in text
    # Relative links are resolved; the old grouped sections are gone
    assert "[JOIN US](https://fund.example/contact-us)" in text
    assert "## Links" not in text and "## Headings" not in text


def test_div_text_is_included():
    html = "<html><body><div class='name'>Puna Bio</div><span>Extremophile bacteria</span></body></html>"
    text = extract_page_content(html).plain_text
    assert "Puna Bio" in text and "Extremophile bacteria" in text


def test_card_wrapped_in_a_link_keeps_the_link():
    html = ('<html><body><a href="https://puna.bio"><div><h3>Puna Bio</h3>'
            '<p>Extremophile bacteria</p></div></a></body></html>')
    text = extract_page_content(html).plain_text
    assert "### Puna Bio\nExtremophile bacteria\n[Link](https://puna.bio)" in text


def test_blank_page_has_no_text():
    assert extract_page_content("<html><head></head><body></body></html>").plain_text == ""


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


def test_site_name_is_read_from_the_page():
    html = ('<html><head><title>Newtopia Portfolio | 90 Latin American Startups</title>'
            '<meta property="og:site_name" content="Newtopia VC"></head><body><p>Hi</p></body></html>')
    page = extract_page_content(html)
    assert page.site_name == "Newtopia VC"
    assert page.title == "Newtopia Portfolio | 90 Latin American Startups"


# --- Several targets in one process ----------------------------------------------

@pytest.fixture
def fake_vllm():
    """Local OpenAI-compatible server with keep-alive connections, like vLLM.
    Every reply is plain text, so runs end quickly without opening a browser."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = _json.dumps({
                "id": "x", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "No companies found."}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_consecutive_targets_do_not_share_a_closed_event_loop(monkeypatch, tmp_path, fake_vllm):
    """Replays the batch failure: 13 of 27 targets hit "RuntimeError: Event loop is closed"
    because one cached OpenAI client was reused across asyncio.run() calls."""
    from pipeline import parse_target, run_target

    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("src.core.config.VLLM_BASE_URL", fake_vllm)
    reports = [
        run_target(parse_target(f"portfolio:https://fund{i}.example"), max_turns=5, mode="agent")
        for i in range(3)
    ]
    assert len(reports) == 3   # before the fix, the second target raised RuntimeError
    assert all(r.items == 0 for r in reports)
