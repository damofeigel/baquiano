"""Tests for the record tools as a small model actually calls them.

Includes a run through the real Agents SDK loop with a scripted fake model, so
tool dispatch and the finish() stop are exercised without a vLLM server.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from agents import Agent, Model, ModelResponse, Runner, Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from src.core.llm import OneToolCallPerTurn
from src.custom_agents.portfolio_agent import agent as agent_module
from src.custom_agents.portfolio_agent.recorder import (
    RECORD_COMPANIES_SCHEMA,
    PortfolioRecorder,
    looks_like_broken_tool_call,
    parse_companies_args,
    salvage_companies,
)

# The shape from the vLLM log: companies sent as an escaped string, cut off
# mid-string ("Unterminated string starting at: line 1 column 58 (char 57)").
TRUNCATED_CALL = (
    '{"name": "record_companies", "parameters": {"companies": "[{\\"name\\": '
    '\\"Alkemio\\", \\"website\\": \\"https://alkemio.tech\\"}, '
    '{\\"name\\": \\"Antarka\\"}, {\\"name\\": \\"Bitgenia\\", '
    '\\"description\\": \\"AI-powered platform for clin'
)


# --- Lenient argument parsing ---------------------------------------------------

def test_parses_the_documented_shape():
    companies, problems = parse_companies_args(json.dumps({
        "companies": [
            {"name": "Alkemio", "website": "https://alkemio.tech", "sector": "biotech"},
            {"name": "Antarka"},
        ]
    }))
    assert [c.name for c in companies] == ["Alkemio", "Antarka"]
    assert companies[0].website == "https://alkemio.tech"
    assert problems == []


def test_parses_shapes_small_models_produce():
    shapes = [
        json.dumps({"companies": ["Alkemio", "Antarka"]}),                      # names only
        json.dumps({"companies": json.dumps([{"name": "Alkemio"}, {"name": "Antarka"}])}),  # stringified
        json.dumps([{"name": "Alkemio"}, {"name": "Antarka"}]),                 # bare list
    ]
    for raw in shapes:
        companies, problems = parse_companies_args(raw)
        assert [c.name for c in companies] == ["Alkemio", "Antarka"], raw
        assert problems == [], raw

    companies, _ = parse_companies_args(json.dumps({"name": "Alkemio"}))       # single object
    assert [c.name for c in companies] == ["Alkemio"]


def test_bad_items_are_reported_but_good_ones_kept():
    companies, problems = parse_companies_args(json.dumps({
        "companies": [{"name": "Alkemio"}, {"website": "https://x.com"}, 42]
    }))
    assert [c.name for c in companies] == ["Alkemio"]
    assert len(problems) == 2


def test_invalid_json_is_reported():
    companies, problems = parse_companies_args('{"companies": [{"name": "Alk')
    assert companies == []
    assert "not valid JSON" in problems[0]


def test_schema_is_flat_and_inline():
    text = json.dumps(RECORD_COMPANIES_SCHEMA)
    assert "$ref" not in text and "$defs" not in text and "anyOf" not in text
    assert len(text) < 1_500


def test_done_is_not_recorded_as_an_error():
    rec = PortfolioRecorder()
    rec.add_error("done")
    rec.add_error("Done.")
    rec.add_error("Pagination could not be accessed")
    assert rec.errors == ["Pagination could not be accessed"]


# --- Salvaging cut-off tool calls -------------------------------------------------

def test_truncated_call_matches_the_vllm_error_position():
    with pytest.raises(json.JSONDecodeError) as exc:
        json.loads(TRUNCATED_CALL)
    assert exc.value.pos == 57


def test_salvages_complete_companies_from_truncated_string_call():
    assert looks_like_broken_tool_call(TRUNCATED_CALL)
    companies = salvage_companies(TRUNCATED_CALL)

    assert [c.name for c in companies] == ["Alkemio", "Antarka"]  # Bitgenia was cut off
    assert companies[0].website == "https://alkemio.tech"


def test_salvages_from_truncated_array_call():
    text = '{"name": "record_companies", "parameters": {"companies": [{"name": "Alkemio"}, {"name": "Anta'
    assert [c.name for c in salvage_companies(text)] == ["Alkemio"]


def test_plain_text_replies_are_not_treated_as_broken_calls():
    assert not looks_like_broken_tool_call("I have recorded all the companies.")
    assert salvage_companies('{"name": "navigate_to_url", "parameters": {"url": "https://x') == []


# --- Real SDK loop with a scripted model ----------------------------------------

class ScriptedModel(Model):
    """Returns one pre-written tool call (or a str: plain text reply) per turn."""

    def __init__(self, calls: list[tuple[str, dict] | str]):
        self.calls = list(calls)
        self.turns = 0
        self.inputs: list = []

    async def get_response(self, system_instructions, input, *args, **kwargs) -> ModelResponse:
        self.inputs.append(input)
        step = self.calls[self.turns]
        self.turns += 1
        if isinstance(step, str):
            items = [ResponseOutputMessage(
                id=f"msg_{self.turns}", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text=step, annotations=[])],
            )]
        else:
            # A list of (name, args) is several tool calls in one response
            calls = step if isinstance(step, list) else [step]
            items = [
                ResponseFunctionToolCall(
                    type="function_call", id=f"fc_{self.turns}_{i}", call_id=f"call_{self.turns}_{i}",
                    name=name, arguments=json.dumps(arguments),
                )
                for i, (name, arguments) in enumerate(calls)
            ]
        return ModelResponse(output=items, usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def test_agent_records_in_batches_and_stops_on_finish():
    rec = PortfolioRecorder()
    model = ScriptedModel([
        ("record_fund", {"fund_name": "GRIDX", "portfolio_url": "https://www.gridexponential.com/portfolio"}),
        ("record_companies", {"companies": [{"name": f"Startup {i}"} for i in range(25)]}),
        ("record_companies", {"companies": [f"Startup {i}" for i in range(25, 40)]}),
        ("finish", {"summary": "40 companies"}),
        ("report_error", {"message": "should never be reached"}),
    ])
    agent = Agent(
        name="test", instructions="x", model=model, tools=rec.tools,
        tool_use_behavior={"stop_at_tool_names": ["finish"]},
    )

    asyncio.run(Runner.run(agent, "go", max_turns=10))

    assert model.turns == 4
    assert rec.fund_name == "GRIDX"
    assert len(rec.companies) == 40
    assert rec.errors == []


GRIDX_PAGE = (
    "# GRIDX | Portfolio\n## Headings\n- Alkemio\n- Antarka\n- Beeflow\n- Bitgenia\n"
    "## Links\n- [Visit Website](https://alkemio.tech)"
)


def _agent_with_model(monkeypatch, model, page_text=None):
    """PortfolioAgent driven by a scripted model; page_text simulates pages read."""
    portfolio_agent = agent_module.PortfolioAgent()
    portfolio_agent._worker = portfolio_agent._worker.clone(model=OneToolCallPerTurn(model))
    evidence = portfolio_agent.browser_tools.evidence
    if page_text is not None:
        evidence.add(page_text, is_page=True)
    monkeypatch.setattr(evidence, "reset", lambda: None)  # keep the simulated page
    return portfolio_agent


def test_parallel_plan_with_invented_companies_saves_nothing(monkeypatch, tmp_path):
    """Replays the real failure: one response holding the whole plan, before any page."""
    monkeypatch.setattr(agent_module.config, "OUTPUT_FILE", tmp_path / "out.json")
    plan = [
        ("navigate_to_url", {"url": "https://www.gridexponential.com"}),
        ("record_companies", {"companies": [{"name": f"Company {i}"} for i in range(1, 5)]}),
        ("finish", {"summary": "Portfolio listing complete"}),
    ]
    model = ScriptedModel([
        plan[1:],                                    # record + finish in one response
        ("finish", {}),                              # finish before reading any page
        ("record_fund", {"fund_name": "GRIDX"}),     # fund name never seen either
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model)
    try:
        result = asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=3))
    finally:
        portfolio_agent.close()

    assert result.startups == []
    assert result.fund_name == "Unknown"
    assert not portfolio_agent.recorder.finished
    assert model.turns == 3   # finish was refused, so the run did not stop early
    # The extra finish call in the first response was dropped, not executed
    first_turn_outputs = json.dumps(model.inputs[1], default=str)
    assert "No page has been read yet" in first_turn_outputs
    assert first_turn_outputs.count("function_call_output") == 1


def test_only_companies_seen_on_a_page_are_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_FILE", tmp_path / "out.json")
    model = ScriptedModel([
        ("record_fund", {"fund_name": "GRIDX"}),
        ("record_companies", {"companies": [
            {"name": "Alkemio", "website": "https://alkemio.tech"},
            {"name": "Antarka", "website": "https://antarka-invented.com"},
            {"name": "Company 1", "website": "https://www.company1.com"},
        ]}),
        ("finish", {}),
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        result = asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=5))
    finally:
        portfolio_agent.close()

    by_name = {s.name: s for s in result.startups}
    assert set(by_name) == {"Alkemio", "Antarka"}
    assert by_name["Alkemio"].website == "https://alkemio.tech"
    assert by_name["Antarka"].website is None          # website never seen: dropped
    assert result.fund_name == "GRIDX"
    assert "does not appear on any page" in json.dumps(model.inputs[2], default=str)


def test_agent_recovers_from_a_cut_off_tool_call(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_FILE", tmp_path / "out.json")
    model = ScriptedModel([
        ("record_fund", {"fund_name": "GRIDX"}),
        TRUNCATED_CALL,                                    # vLLM hands this back as text
        ("record_companies", {"companies": [{"name": "Bitgenia"}, {"name": "Beeflow"}]}),
        ("finish", {}),
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        result = asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=10))
    finally:
        portfolio_agent.close()

    assert model.turns == 4
    assert sorted(s.name for s in result.startups) == ["Alkemio", "Antarka", "Beeflow", "Bitgenia"]
    assert result.errors == []
    # The broken reply is dropped from history and replaced by the recovery note
    resumed = json.dumps(model.inputs[2], default=str)
    assert "Unterminated" not in resumed and "AI-powered platform for clin" not in resumed
    assert "was cut off" in resumed
