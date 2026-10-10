"""Tests for per-URL agent selection in pipeline.py (CLI arguments and targets.txt)."""

from __future__ import annotations

import inspect

import pytest

import pipeline
from pipeline import Target, load_targets, parse_target, run_target
from src.custom_agents.base import AgentSpec, RunReport
from src.custom_agents.registry import AGENTS


@pytest.mark.parametrize("spec, expected", [
    ("portfolio:https://fund.example", Target("portfolio", "https://fund.example")),
    ("Portfolio:https://fund.example", Target("portfolio", "https://fund.example")),
    ("portfolio: https://fund.example ", Target("portfolio", "https://fund.example")),
    ("portfolio:fund.example/portafolio", Target("portfolio", "https://fund.example/portafolio")),
    ("portfolio:https://fund.example:8080/x", Target("portfolio", "https://fund.example:8080/x")),
    ("portfolio:https://fund.example/#/portfolio", Target("portfolio", "https://fund.example/#/portfolio")),
])
def test_parse_target(spec, expected):
    assert parse_target(spec) == expected


@pytest.mark.parametrize("spec, message", [
    ("https://fund.example", "missing agent"),
    ("fund.example", "missing agent"),
    ("news:https://fund.example", "unknown agent 'news'"),
    ("portfolio:", "missing URL"),
])
def test_parse_target_rejects_bad_specs(spec, message):
    with pytest.raises(ValueError, match=message) as exc:
        parse_target(spec)
    assert "available: portfolio" in str(exc.value) or message == "missing URL"


def test_cli_specs_support_spaces_and_commas():
    targets = load_targets(["portfolio:https://a.example,portfolio:b.example", "portfolio:c.example"])
    assert [t.url for t in targets] == ["https://a.example", "https://b.example", "https://c.example"]
    assert {t.agent for t in targets} == {"portfolio"}


def test_file_entries_with_comments_and_fragments(tmp_path):
    file = tmp_path / "targets.txt"
    file.write_text(
        "# header comment\n"
        "\n"
        "portfolio:https://www.gridexponential.com   # deep tech\n"
        "portfolio:https://fund.example/#/portfolio\n",
        encoding="utf-8",
    )
    assert load_targets([], file) == [
        Target("portfolio", "https://www.gridexponential.com"),
        Target("portfolio", "https://fund.example/#/portfolio"),
    ]


def test_all_invalid_entries_are_reported_with_line_numbers(tmp_path):
    file = tmp_path / "targets.txt"
    file.write_text("portfolio:https://ok.example\nhttps://no-agent.example\nportfolo:https://typo.example\n")
    with pytest.raises(ValueError) as exc:
        load_targets([], file)
    assert "targets.txt:2: missing agent" in str(exc.value)
    assert "targets.txt:3: unknown agent 'portfolo'" in str(exc.value)


def test_cli_arguments_take_precedence_over_file(tmp_path):
    file = tmp_path / "targets.txt"
    file.write_text("portfolio:https://from-file.example\n")
    assert load_targets(["portfolio:https://from-cli.example"], file) == [
        Target("portfolio", "https://from-cli.example")
    ]


def test_missing_or_comment_only_file_gives_no_targets(tmp_path):
    assert load_targets([], tmp_path / "missing.txt") == []
    file = tmp_path / "targets.txt"
    file.write_text("# only comments\n\n")
    assert load_targets([], file) == []


# --- Registry and dispatch ----------------------------------------------------------

def test_registered_agents_follow_the_contract():
    assert "portfolio" in AGENTS
    for name, spec in AGENTS.items():
        assert spec.name == name
        assert spec.description
        assert inspect.iscoroutinefunction(spec.run)


def test_run_target_dispatches_to_the_named_agent_with_options(monkeypatch):
    calls = []

    async def fake_run(url, **options):
        calls.append((url, options))
        return RunReport(summary="2 news item(s)", items=2)

    monkeypatch.setitem(AGENTS, "news", AgentSpec(name="news", description="test", run=fake_run))

    target = parse_target("news:https://press.example")
    report = run_target(target, max_turns=5, mode=None)

    assert report.items == 2
    assert calls == [("https://press.example", {"max_turns": 5, "mode": None})]
    assert pipeline.AGENTS["news"].name == "news"  # pipeline reads the same registry
