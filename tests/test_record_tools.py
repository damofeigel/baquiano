"""Tests for the record tools as a small model actually calls them.

Includes a run through the real Agents SDK loop with a scripted fake model, so
tool dispatch and the finish() stop are exercised without a vLLM server.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from agents import (
    Agent, Model, ModelBehaviorError, ModelResponse,
    Runner, ToolsToFinalOutputResult, 
    Usage, function_tool
)
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from src.core.llm import OneToolCallPerTurn, clean_tool_name
from src.custom_agents.portfolio_agent import agent as agent_module
from src.custom_agents.portfolio_agent.models import StartupInfo
from src.custom_agents.portfolio_agent.tools import Evidence, url_key
from src.custom_agents.portfolio_agent.recorder import (
    MAX_FINISH_REFUSALS,
    looks_like_navigation,
    website_matches_name,
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

LENGTH = "<reply cut off at max_tokens>"


class ScriptedModel(Model):
    """Returns one pre-written tool call (or a str: plain text reply) per turn."""

    def __init__(self, calls: list[tuple[str, dict] | str]):
        self.calls = list(calls)
        self.turns = 0
        self.inputs: list = []
        self.settings: list = []

    async def get_response(self, system_instructions, input, model_settings=None, *args, **kwargs) -> ModelResponse:
        self.inputs.append(input)
        self.settings.append(model_settings)
        step = self.calls[self.turns]
        self.turns += 1
        if step == LENGTH:
            # What OpenAIChatCompletionsModel raises when vLLM hits max_tokens mid-call
            raise ModelBehaviorError(
                "Chat Completions response terminated with finish_reason='length' "
                "but produced no assistant text, tool call, or refusal."
            )
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
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
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
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
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
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
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
    # It is told which companies are saved, so it continues instead of restarting
    assert "Saved so far (2): Alkemio, Antarka" in resumed


# --- Portfolio page must be opened before finish --------------------------------

HOME = "https://www.gridexponential.com"
PORTFOLIO = "https://www.gridexponential.com/portfolio"
PAGES = {
    HOME: "# GRIDX\n## Headings\n- Meet the startups\n- Stämm\n- Puna Bio\n"
          "## Links\n- [Portfolio](https://www.gridexponential.com/portfolio)",
    PORTFOLIO: "# GRIDX | Portfolio\n## Headings\n- Stämm\n- Puna Bio\n- Alkemio\n- Antarka",
}


def _guarded_agent(model):
    """Recorder with evidence, a fake navigate_to_url serving PAGES, and the
    same stop rule as PortfolioAgent (stop only on an accepted finish)."""
    evidence = Evidence()
    rec = PortfolioRecorder(evidence=evidence)

    @function_tool
    def navigate_to_url(url: str) -> str:
        """Open a page."""
        evidence.mark_attempted(url)
        if url not in PAGES:
            return json.dumps({"error": "TimeoutError: page did not load"})
        evidence.add(PAGES[url], is_page=True, url=url)
        return PAGES[url]

    def stop_when_finished(ctx, results):
        return ToolsToFinalOutputResult(is_final_output=rec.finished, final_output="finished" if rec.finished else None)

    agent = Agent(
        name="test", instructions="x", model=OneToolCallPerTurn(model),
        tools=[navigate_to_url, *rec.tools], tool_use_behavior=stop_when_finished,
    )
    return agent, rec


def test_replay_finish_refused_until_portfolio_page_is_opened():
    """Replays the GridX agent-mode run: homepage featured companies, then finish."""
    model = ScriptedModel([
        ("navigate_to_url", {"url": HOME}),
        ("record_fund", {"fund_name": "GRIDX", "portfolio_url": PORTFOLIO}),
        ("record_companies", {"companies": ["Stämm", "Puna Bio"]}),
        ("finish", {"summary": "done"}),              # refused: portfolio not opened
        ("navigate_to_url", {"url": PORTFOLIO}),
        ("record_companies", {"companies": ["Alkemio", "Antarka"]}),
        ("finish", {"summary": "done"}),              # accepted
    ])
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=10))

    assert model.turns == 7
    assert rec.finished
    assert sorted(c.name for c in rec.companies) == ["Alkemio", "Antarka", "Puna Bio", "Stämm"]
    assert rec.errors == []
    history = json.dumps(model.inputs[-1], default=str)
    assert "next_step" in history and "Open https://www.gridexponential.com/portfolio" in history
    assert "Not finished: you recorded the portfolio page" in history


def test_finish_accepted_with_error_after_repeated_refusals():
    model = ScriptedModel(
        [("navigate_to_url", {"url": HOME}),
         ("record_fund", {"fund_name": "GRIDX", "portfolio_url": PORTFOLIO})]
        + [("finish", {})] * (MAX_FINISH_REFUSALS + 1)
    )
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=10))

    assert rec.finished
    assert model.turns == 2 + MAX_FINISH_REFUSALS + 1
    assert rec.errors == [f"The portfolio page {PORTFOLIO} was never opened; the list may be incomplete."]


def test_dead_portfolio_link_does_not_block_finish():
    dead = "https://www.gridexponential.com/old-portfolio"
    model = ScriptedModel([
        ("navigate_to_url", {"url": HOME}),
        ("record_fund", {"fund_name": "GRIDX", "portfolio_url": dead}),
        ("navigate_to_url", {"url": dead}),           # fails to load
        ("finish", {}),                               # accepted: opening was tried
    ])
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=10))

    assert rec.finished and model.turns == 4


def test_no_next_step_when_portfolio_is_the_page_already_open():
    model = ScriptedModel([
        ("navigate_to_url", {"url": PORTFOLIO}),
        ("record_fund", {"fund_name": "GRIDX", "portfolio_url": "gridexponential.com/portfolio/"}),
        ("finish", {}),
    ])
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=5))

    assert rec.finished and model.turns == 3
    assert "next_step" not in json.dumps(model.inputs[2], default=str)


def test_url_key_ignores_scheme_www_slash_and_fragment():
    variants = [
        "https://www.gridexponential.com/portfolio",
        "http://gridexponential.com/portfolio/",
        "gridexponential.com/portfolio#top",
        "https://WWW.GridExponential.com/portfolio",
    ]
    assert len({url_key(v) for v in variants}) == 1
    assert url_key("https://fund.example/portfolio?page=2") != url_key("https://fund.example/portfolio")


def test_social_network_websites_are_dropped():
    rec = PortfolioRecorder()
    rec.add_companies([
        StartupInfo(name="CASPR Biotech", website="https://www.linkedin.com/company/casprbio/"),
        StartupInfo(name="Puna Bio", website="https://puna.bio"),
    ])
    by_name = {c.name: c for c in rec.companies}
    assert by_name["CASPR Biotech"].website is None
    assert by_name["Puna Bio"].website == "https://puna.bio"



def test_values_that_are_no_website_are_dropped_and_never_break_a_batch():
    """A broken link used to raise in the middle of add_companies, after some companies were added."""
    evidence = Evidence()
    evidence.add("Kilimo kilimo.com Brokenco Puna Bio puna.bio Beeflow Glaia hola@gmail.com", is_page=True, url=PORTFOLIO)
    rec = PortfolioRecorder(evidence=evidence)
    accepted, problems = rec.check_evidence([
        StartupInfo(name="Kilimo", website="https://kilimo.com"),
        StartupInfo(name="Brokenco", website="https://[brokenco"),
        StartupInfo(name="Puna Bio", website="https://[website]"),          # a small model's placeholder
        StartupInfo(name="Beeflow", website="tel:+5491111111111"),
        StartupInfo(name="Glaia", website="hola@gmail.com"),
    ])
    assert problems == []
    assert rec.add_companies(accepted) == (5, 0)
    assert {c.name: c.website for c in rec.companies} == {
        "Kilimo": "https://kilimo.com", "Brokenco": None, "Puna Bio": None, "Beeflow": None, "Glaia": None}


def test_phone_numbers_and_emails_do_not_merge_companies():
    rec = PortfolioRecorder()
    rec.add_companies([StartupInfo(name="Kilimo", website="tel:+5491111111111"),
                       StartupInfo(name="Beeflow", website="tel:+5492222222222"),
                       StartupInfo(name="Puna Bio", website="hola@gmail.com"),
                       StartupInfo(name="Glaia", website="contacto@gmail.com")])
    assert [c.name for c in rec.companies] == ["Kilimo", "Beeflow", "Puna Bio", "Glaia"]


def test_hosts_ignore_ports_users_and_case():
    from src.custom_agents.portfolio_agent.heuristics import _same_site, is_social_url
    assert is_social_url("https://user@linkedin.com/company/x") and is_social_url("ar.LinkedIn.com/in/x")
    assert _same_site("https://acme.com:443/", "https://acme.com") and _same_site("https://Acme.com", "acme.com")
    assert not is_social_url("https://[broken") and not _same_site("https://[broken", "https://acme.com")
    assert url_key("https://acme.com:443/portfolio") == url_key("https://user@www.Acme.com/portfolio/")
    assert url_key("mailto:hola@acme.com") == "hola@acme.com"          # not a website: as written


# --- Knowing where to continue ----------------------------------------------------

def test_batch_with_nothing_new_lists_saved_names():
    """Replays the post-recovery loop: the model resends companies already saved."""
    model = ScriptedModel([
        ("navigate_to_url", {"url": PORTFOLIO}),
        ("record_companies", {"companies": ["Stämm", "Puna Bio"]}),
        ("record_companies", {"companies": ["Stämm", "Puna Bio"]}),   # nothing new
        ("finish", {}),
    ])
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=10))

    first, second = (json.dumps(model.inputs[i], default=str, ensure_ascii=False) for i in (2, 3))
    assert "already saved" not in first             # new companies: no note
    assert "All of these were already saved. Saved so far (2): Stämm, Puna Bio." in second


def test_saved_names_note_is_capped():
    rec = PortfolioRecorder()
    rec.add_companies([StartupInfo(name=f"Startup number {i}") for i in range(500)])
    note = rec.saved_names_note(max_chars=200)
    assert note.startswith("Saved so far (500): Startup number 0, ")
    assert note.endswith(", ....") and len(note) < 260


# --- tool_choice ------------------------------------------------------------------

@pytest.mark.parametrize("setting, expected_choice, expected_reset, expected_parallel", [
    ("", None, True, None),
    ("required", "required", False, False),
])
def test_tool_choice_setting_is_sent_on_every_turn(
    monkeypatch, tmp_path, setting, expected_choice, expected_reset, expected_parallel,
):
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(agent_module.config, "LLM_TOOL_CHOICE", setting)
    model = ScriptedModel([
        ("record_fund", {"fund_name": "GRIDX"}),
        ("record_companies", {"companies": ["Alkemio"]}),
        ("finish", {}),
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=5))
    finally:
        portfolio_agent.close()

    assert portfolio_agent._worker.reset_tool_choice is expected_reset
    # Not just the first turn: the SDK would reset it to auto after a tool call
    assert [s.tool_choice for s in model.settings] == [expected_choice] * 3
    assert {s.parallel_tool_calls for s in model.settings} == {expected_parallel}



# --- Replies cut off with no usable output ------------------------------------------

def test_agent_recovers_from_reply_cut_off_with_no_tool_call(monkeypatch, tmp_path):
    """Replays the crash: finish_reason='length' with nothing usable raised ModelBehaviorError."""
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    model = ScriptedModel([
        ("record_fund", {"fund_name": "GRIDX"}),
        ("record_companies", {"companies": ["Alkemio"]}),
        LENGTH,                                          # used to crash the whole fund
        ("record_companies", {"companies": ["Antarka"]}),
        ("finish", {}),
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        result = asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=10))
    finally:
        portfolio_agent.close()

    assert sorted(s.name for s in result.startups) == ["Alkemio", "Antarka"]
    assert result.errors == []
    resumed = json.dumps(model.inputs[3], default=str)
    assert "Reply with exactly ONE tool call" in resumed
    assert "Saved so far (1): Alkemio" in resumed
    assert "record_fund" in resumed                     # earlier history kept


def test_repeated_cut_offs_end_with_an_error_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    model = ScriptedModel([LENGTH] * (agent_module.MAX_RECOVERIES + 1))
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        result = asyncio.run(portfolio_agent.run("https://gridx.example", mode="agent", max_turns=10))
    finally:
        portfolio_agent.close()

    assert result.startups == []
    assert any("repeatedly replied without a usable tool call" in e for e in result.errors)


# --- Blank start page is not a page read ------------------------------------------

def test_blank_start_page_is_not_counted_as_read():
    """Replays the run that began with get_current_page_content on about:blank."""
    from types import SimpleNamespace
    from src.custom_agents.portfolio_agent.tools import BrowserTools

    bt = BrowserTools()
    blank = SimpleNamespace(url="about:blank", plain_text="")
    bt._browser = SimpleNamespace(get_clean_content=lambda: blank, page=blank, close=lambda: None)
    try:
        reply = json.loads(bt._invoke_sync("get_clean_content"))
    finally:
        bt.close()

    assert "No page is open yet" in reply["error"]
    assert bt.evidence.pages_read == 0


# --- Websites from the wrong card, fund links, navigation as names -----------------

@pytest.mark.parametrize("name, website", [
    # Correct pairs from GridX, including brand variants of the name
    ("Algaebio+", "https://algaebiomas.com"),
    ("Tomorrow", "https://tomorrowfoods.com.ar"),
    ("Levya", "https://levyabio.com/"),
    ("CellCo", "https://the-cellco.com"),
    ("BioClé", "https://biocle.tech/"),
    ("Eywa Biotech", "https://eywa.bio"),
    ("Puna Bio", "https://puna.bio"),
    ("Biodiversity Intelligence", "https://biodiversityintell.com"),
    ("Hiamet S.A", "https://hiamet.com"),
])
def test_matching_websites_are_kept(name, website):
    assert website_matches_name(name, website)


@pytest.mark.parametrize("name, website", [
    # Shifted pairs from the agent-mode run: each got the next card's website
    ("Bioplastix", "https://bitgenia.com"),
    ("BreakPET", "https://bsafebiotech.com"),
    ("CellCo", "https://cellrep.bio"),
    ("Enteria", "https://www.enzyva-bio.com/"),
    ("Tomorrow", "https://ubique.bio"),
    ("Syocin Bio", "https://tesabio.ai/"),
])
def test_websites_from_another_card_are_rejected(name, website):
    assert not website_matches_name(name, website)


def test_page_navigation_is_not_a_company_name():
    for name in ["JOIN US", "Visit our Linkedin profile", "Visit our Instagram profile",
                 "Contacto", "Ver más", "Síguenos"]:
        assert looks_like_navigation(name), name
    for name in ["Alkemio", "Visitly", "Tomorrow", "Contactless Labs"]:
        assert not looks_like_navigation(name), name


def test_agent_records_are_vetted_for_website_fit_fund_links_and_navigation():
    """Replays the end of the agent-mode output against a page that shows them all."""
    evidence = Evidence()
    evidence.add(
        "### Zavia Bio [Visit Website](https://zevbiotech.com) ### ZEV Biotech "
        "[JOIN US](https://www.gridexponential.com/contact-us) Visit our Linkedin profile "
        "### Tomorrow [Visit Website](https://ubique.bio) ### Puna Bio [Visit Website](https://puna.bio)",
        is_page=True, url=PORTFOLIO,
    )
    rec = PortfolioRecorder(evidence=evidence)
    rec.site_url = HOME
    accepted, problems = rec.check_evidence([
        StartupInfo(name="Zavia Bio", website="https://zevbiotech.com"),          # neighbour's site
        StartupInfo(name="Tomorrow", website="https://ubique.bio"),               # neighbour's site
        StartupInfo(name="Puna Bio", website="https://puna.bio"),                 # correct
        StartupInfo(name="JOIN US"),                                              # navigation
        StartupInfo(name="Visit our Linkedin profile"),                           # navigation
    ])
    rec.add_companies(accepted + [
        StartupInfo(name="ZEV Biotech", website="https://www.gridexponential.com/contact-us"),
    ])

    websites = {c.name: c.website for c in rec.companies}
    assert websites == {"Zavia Bio": None, "Tomorrow": None, "Puna Bio": "https://puna.bio", "ZEV Biotech": None}
    assert sum("site navigation" in p for p in problems) == 2



def test_sectors_the_page_does_not_show_are_dropped():
    """A sector must be the fund's own label: the model's guess from the description is dropped."""
    evidence = Evidence()
    evidence.add("### Puna Bio Agri·food·land use Extremophile bacteria for crops "
                 "### Stämm Programmable biomanufacturing for the food supply chain", is_page=True, url=PORTFOLIO)
    rec = PortfolioRecorder(evidence=evidence)
    accepted, problems = rec.check_evidence([
        StartupInfo(name="Puna Bio", sector="Agri·food·land use"),   # label shown on the card
        StartupInfo(name="Stämm", sector="biotechnology"),            # guessed from the description
        StartupInfo(name="Puna Bio", sector="AI"),                    # 'ai' only appears inside 'chain'
    ])

    assert [(c.name, c.sector) for c in accepted] == [
        ("Puna Bio", "Agri·food·land use"), ("Stämm", None), ("Puna Bio", None)]
    assert problems == []                                             # the company itself is kept


# --- Tool names with chat-format residue (gpt-oss / harmony) ------------------------

@pytest.mark.parametrize("raw, clean", [
    ("finish<|channel|>", "finish"),
    ("record_companies<|channel|>commentary", "record_companies"),
    ("functions.navigate_to_url", "navigate_to_url"),
    ("functions.finish<|channel|>commentary json", "finish"),
    ("record_fund", "record_fund"),
])
def test_clean_tool_name(raw, clean):
    assert clean_tool_name(raw) == clean


def test_harmony_tool_names_reach_the_right_tools():
    """Replays 'Tool finish<|channel|> not found in agent PortfolioFinderWorker'."""
    model = ScriptedModel([
        ("functions.navigate_to_url", {"url": PORTFOLIO}),
        ("record_companies<|channel|>commentary", {"companies": ["Stämm", "Puna Bio"]}),
        ("finish<|channel|>", {}),
    ])
    agent, rec = _guarded_agent(model)
    asyncio.run(Runner.run(agent, "go", max_turns=5))

    assert rec.finished
    assert model.turns == 3
    assert sorted(c.name for c in rec.companies) == ["Puna Bio", "Stämm"]


# --- Plain-text replies (gpt-oss answers in prose on its "final" channel) -----------

def test_text_reply_is_followed_by_a_nudge_not_the_end_of_the_run():
    """Replays the newtopia.vc run: the model read the page, then answered in text."""
    model = ScriptedModel([
        ("navigate_to_url", {"url": PORTFOLIO}),
        "The portfolio includes Stämm and Puna Bio.",          # used to end the run
        ("record_companies", {"companies": ["Stämm", "Puna Bio"]}),
        ("finish", {}),
    ])
    agent, rec = _guarded_agent(model)
    portfolio_agent = agent_module.PortfolioAgent.__new__(agent_module.PortfolioAgent)
    portfolio_agent._worker, portfolio_agent.recorder = agent, rec
    asyncio.run(portfolio_agent._run_worker("go", max_turns=10))

    assert rec.finished
    assert sorted(c.name for c in rec.companies) == ["Puna Bio", "Stämm"]
    nudged = json.dumps(model.inputs[2], default=str, ensure_ascii=False)
    assert "You replied with text instead of a tool call" in nudged
    assert "The portfolio includes Stämm and Puna Bio." in nudged    # its reply is kept


def test_repeated_text_replies_end_with_an_error():
    model = ScriptedModel([("navigate_to_url", {"url": PORTFOLIO})] + ["Done."] * (agent_module.MAX_RECOVERIES + 1))
    agent, rec = _guarded_agent(model)
    portfolio_agent = agent_module.PortfolioAgent.__new__(agent_module.PortfolioAgent)
    portfolio_agent._worker, portfolio_agent.recorder = agent, rec
    asyncio.run(portfolio_agent._run_worker("go", max_turns=10))

    assert not rec.finished
    assert any("plain text" in e for e in rec.errors)


def test_fund_named_from_the_page_when_agent_never_records_it(monkeypatch, tmp_path):
    """Replays the newtopia.vc agent-mode run: companies saved, record_fund never called."""
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)
    model = ScriptedModel([
        ("record_companies", {"companies": ["Alkemio"]}),
        ("finish", {}),
    ])
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    portfolio_agent.browser_tools.evidence.site_name = "Newtopia VC"
    try:
        result = asyncio.run(portfolio_agent.run("https://newtopia.example", mode="agent", max_turns=5))
    finally:
        portfolio_agent.close()

    assert result.fund_name == "Newtopia VC"
    assert "Fund name was not identified." not in result.errors


def test_name_variants_from_regional_sites_are_merged():
    rec = PortfolioRecorder()
    rec.add_companies([
        StartupInfo(name="Heartbest Foods", website="https://www.heartbest.com.mx"),
        StartupInfo(name="Botanical Solutions", website="https://botanical-solution.com"),
        StartupInfo(name="Agree", website="https://www.agree.ag"),
    ])
    rec.add_companies([
        StartupInfo(name="Heartbest", country="Mexico"),            # LatAm site, no website
        StartupInfo(name="Botanical Solution", country="Chile"),
        StartupInfo(name="Agreena"),                                # different company
    ])
    by_name = {c.name: c for c in rec.companies}
    assert set(by_name) == {"Heartbest Foods", "Botanical Solutions", "Agree", "Agreena"}
    assert by_name["Heartbest Foods"].country == "Mexico"


def test_hybrid_text_replies_keep_prepass_results_without_error(monkeypatch, tmp_path):
    """Replays theyieldlab.com in hybrid mode: gpt-oss only answered in text."""
    monkeypatch.setattr(agent_module.config, "OUTPUT_DIR", tmp_path)

    async def fake_prepass(browser_tools, url):
        return agent_module.PrepassResult(
            fund_name_hint="The Yield Lab", portfolio_url=url, portfolio_urls=[url],
            companies=[StartupInfo(name="Kilimo"), StartupInfo(name="Auravant")],
        )

    monkeypatch.setattr(agent_module, "run_prepass", fake_prepass)
    model = ScriptedModel(["The list looks complete."] * (agent_module.MAX_RECOVERIES + 1))
    portfolio_agent = _agent_with_model(monkeypatch, model, page_text=GRIDX_PAGE)
    try:
        result = asyncio.run(portfolio_agent.run("https://theyieldlab.example", mode="hybrid", max_turns=10))
    finally:
        portfolio_agent.close()

    assert sorted(s.name for s in result.startups) == ["Auravant", "Kilimo"]
    assert result.errors == []
    assert "If nothing is missing, call finish" in json.dumps(model.inputs[1], default=str)
