"""Tests for the record tools as a small model actually calls them.

Includes a run through the real Agents SDK loop with a scripted fake model, so
tool dispatch and the finish() stop are exercised without a vLLM server.
"""

from __future__ import annotations

import asyncio
import json

from agents import Agent, Model, ModelResponse, Runner, Usage
from openai.types.responses import ResponseFunctionToolCall

from src.custom_agents.portfolio_agent.recorder import (
    RECORD_COMPANIES_SCHEMA,
    PortfolioRecorder,
    parse_companies_args,
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


# --- Real SDK loop with a scripted model ----------------------------------------

class ScriptedModel(Model):
    """Returns one pre-written tool call per turn."""

    def __init__(self, calls: list[tuple[str, dict]]):
        self.calls = list(calls)
        self.turns = 0

    async def get_response(self, *args, **kwargs) -> ModelResponse:
        name, arguments = self.calls[self.turns]
        self.turns += 1
        call = ResponseFunctionToolCall(
            type="function_call", id=f"fc_{self.turns}", call_id=f"call_{self.turns}",
            name=name, arguments=json.dumps(arguments),
        )
        return ModelResponse(output=[call], usage=Usage(), response_id=None)

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
