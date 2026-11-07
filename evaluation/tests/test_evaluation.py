"""Tests for the evaluation scripts: scores, settings reaching the model, replay and graphs."""

from __future__ import annotations

import csv
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402
import plot_results  # noqa: E402
import run_detection  # noqa: E402
import run_matching  # noqa: E402
from common import Gold, applied, rates, score_detection  # noqa: E402
from snapshot_pages import page_key, strip_scripts  # noqa: E402
from src.core import config  # noqa: E402


def _gold(**kwargs) -> Gold:
    return Gold(fund="f", reviewed=True, **kwargs)


def test_detection_score_counts_right_wrong_missed_and_undecided():
    gold = _gold(companies={"Puna Bio": "https://puna.bio", "Kilimo": None, "Belvo": None},
                 not_companies={"Lebanon"}, undecided={"Maybe"})
    found = [{"name": "puna bio", "website": "https://www.puna.bio/es"}, {"name": "Kilimo"},
             {"name": "Lebanon"}, {"name": "72"}, {"name": "Maybe"}]
    score = score_detection(found, gold)
    assert (score["correct"], score["wrong"], score["missed"], score["undecided"]) == (2, 2, 1, 1)
    assert score["wrong_names"] == ["72", "Lebanon"] and score["missed_names"] == ["Belvo"]
    assert (score["websites_right"], score["websites_checked"]) == (1, 1)


def test_a_name_variant_counts_once_and_a_wrong_website_is_seen():
    gold = _gold(companies={"Nuvemshop": "https://nuvemshop.com.br"})
    score = score_detection([{"name": "Nuvemshop.com.br", "website": "https://other.com"}, {"name": "Nuvemshop"}], gold)
    assert (score["correct"], score["wrong"]) == (1, 0)
    assert (score["websites_right"], score["websites_checked"]) == (0, 1)


def test_finding_nothing_on_a_page_with_nothing_is_not_scored_as_a_failure():
    assert score_detection([], _gold())["wrong"] == 0
    assert rates(0, 0, 0) == (None, None, None)


@pytest.mark.parametrize("counts, expected", [
    ((8, 2, 0), (0.8, 1.0, 8 / 9)),
    ((0, 3, 0), (0.0, None, 0.0)),
    ((0, 0, 3), (None, 0.0, 0.0)),
])
def test_rates(counts, expected):
    assert rates(*counts) == pytest.approx(expected)


def test_gold_file_rows(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"reviewed": True}))
    (tmp_path / "gold.csv").write_text("keep,name,website,found_by,note\nyes,A,https://a.com,,\nno,B,,,\n?,C,,,\n,D,,,\n")
    gold = common.load_gold(tmp_path)
    assert gold.reviewed and gold.companies == {"A": "https://a.com"}
    assert gold.not_companies == {"B"} and gold.undecided == {"C", "D"}


def test_a_setting_applies_only_while_in_use():
    before = config.VLLM_MODEL, config.LLM_TEMPERATURE
    with applied({"model": "other/model", "temperature": 0.7}):
        assert (config.VLLM_MODEL, config.LLM_TEMPERATURE) == ("other/model", 0.7)
    assert (config.VLLM_MODEL, config.LLM_TEMPERATURE) == before


@pytest.mark.parametrize("a, b", [
    ("https://www.acme.com/portfolio/", "http://acme.com/portfolio#top"),
    ("https://acme.com", "https://acme.com/"),
])
def test_page_key_ignores_how_a_link_is_written(a, b):
    assert page_key(a) == page_key(b)


def test_scripts_are_removed_from_saved_pages():
    assert strip_scripts('<p>a</p><SCRIPT src="x">var b;\n</script ><p>c</p>') == "<p>a</p><p>c</p>"


def _pair(label="match", **sides) -> dict:
    base = {f"{side}_{c}": "" for side in ("left", "right") for c in ("website", "country", "description")}
    return {"pair_id": "1", "kind": "unsure", "label": label, "left_name": "Tapi", "right_name": "Tapi",
            "left_funds": "Newtopia VC", "right_funds": "Kaszek", **base, **sides}


def test_same_website_needs_no_model():
    def must_not_run(*_):
        raise AssertionError("the model must not be asked")

    pair = _pair(left_website="https://tapi.la", right_website="https://www.tapi.la/es")
    assert run_matching.decide(pair, must_not_run) == ("match", "rules")


def test_rules_leave_a_bare_name_match_unsure_and_the_model_answers_with_the_judges_checks():
    pair = _pair(left_description="Payments for Latin America", right_description="Payments for Latin America")
    assert run_matching.decide(_pair(), None) == ("unsure", "rules")
    answer = {"decision": "match", "confidence": "high", "reason": "Same business",
              "evidence": [{"record": "A", "quote": "Payments for Latin America"},
                           {"record": "B", "quote": "Payments for Latin America"}]}
    assert run_matching.decide(_pair(), lambda *_: answer)[1] == "model"
    weak = {**answer, "confidence": "low"}
    decision, _ = run_matching.decide(pair, lambda *_: weak)
    assert decision in ("match", "unsure")     # rules may already be sure; low confidence never makes a match
    assert run_matching.decide(_pair(), lambda *_: weak) == ("unsure", "model")


class _FakeModelServer(http.server.BaseHTTPRequestHandler):
    """A stand-in for vLLM: remembers each request and always answers with the finish tool."""
    requests: list[dict] = []

    def log_message(self, *args):
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "test/model", "object": "model"}]})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(request)
        call = {"id": "c1", "type": "function", "function": {"name": "finish", "arguments": '{"summary": "done"}'}}
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
        if request.get("response_format"):
            message = {"role": "assistant", "content": json.dumps(
                {"decision": "unsure", "confidence": "low", "reason": "Too little", "evidence": []})}
        self._send({"id": "x", "object": "chat.completion", "created": 1, "model": request["model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                              "completion_tokens_details": {"reasoning_tokens": 3}}})


@pytest.fixture
def model_server():
    _FakeModelServer.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_matching_sends_the_settings_to_the_server(model_server, tmp_path, monkeypatch):
    monkeypatch.setattr(run_matching, "RESULTS_FILE", tmp_path / "m.jsonl")
    setting = {"name": "s", "use_model": True, "model": "test/model", "base_url": model_server, "temperature": 0.2,
               "extra_body": {"reasoning_effort": "low"}}
    with applied(setting):
        run_matching.run_setting(setting, [_pair()], set())
    sent = _FakeModelServer.requests[0]
    assert (sent["model"], sent["temperature"], sent["reasoning_effort"]) == ("test/model", 0.2, "low")
    row = common.read_results(tmp_path / "m.jsonl")[0]
    assert row["decision"] == "unsure" and row["usage"]["reasoning_tokens"] == 3


@pytest.fixture
def saved_fund(tmp_path) -> Path:
    fund = tmp_path / "fund"
    (fund / "pages").mkdir(parents=True)
    cards = "".join(f'<div class="c"><img alt="{n}"><a href="https://{n.lower()}.example">{n}</a></div>'
                    for n in ["Agribody", "Holganix", "Hydrosat", "Lepidext"])
    (fund / "pages" / "portfolio.html").write_text(f'<html><body><h1>Portfolio</h1><div class="g">{cards}</div></body></html>')
    (fund / "meta.json").write_text(json.dumps({
        "url": "https://fund.example/portfolio", "reviewed": True, "pages": {"fund.example/portfolio": "portfolio.html"}}))
    (fund / "gold.csv").write_text("keep,name,website,found_by,note\nyes,Agribody,https://agribody.example,,\n"
                                   "yes,Holganix,,,\nyes,Hydrosat,,,\nyes,Lepidext,,,\nyes,Missing Co,,,\n")
    return fund


@pytest.mark.browser
def test_parser_only_reads_the_saved_page_and_writes_nothing_to_output(saved_fund, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "agent_output")
    row = run_detection.run_one({"name": "parser only", "mode": "deterministic"}, saved_fund, 0)
    assert "failed" not in row, row.get("failed")
    assert (row["correct"], row["wrong"], row["missed"]) == (4, 0, 1)
    assert row["usage"]["calls"] == 0


@pytest.mark.browser
def test_the_agent_gets_the_settings_and_stays_on_the_saved_pages(saved_fund, tmp_path, monkeypatch, model_server):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "agent_output")
    setting = {"name": "s", "mode": "hybrid", "model": "test/model", "base_url": model_server, "temperature": 0.2,
               "extra_body": {"reasoning_effort": "low"}, "max_turns": 3}
    with applied(setting):
        row = run_detection.run_one(setting, saved_fund, 0)
    assert "failed" not in row, row.get("failed")
    sent = _FakeModelServer.requests[0]
    assert (sent["model"], sent["temperature"], sent["reasoning_effort"]) == ("test/model", 0.2, "low")
    assert row["correct"] == 4 and row["usage"]["calls"] >= 1 and row["usage"]["reasoning_tokens"] >= 3


def _detection_row(setting, fund, correct, wrong, missed, seconds, reviewed=True):
    return {"setting": setting, "fund": fund, "repeat": 0, "model": None, "reviewed": reviewed, "correct": correct,
            "wrong": wrong, "missed": missed, "undecided": 0, "websites_right": 1, "websites_checked": 2,
            "seconds": seconds, "usage": {"calls": 2, "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}


def test_detection_summary_adds_up_funds():
    rows = [_detection_row("a", "x", 8, 2, 0, 10), _detection_row("a", "y", 2, 0, 10, 30)]
    summary = plot_results.summarize_detection(rows)[0]
    assert (summary["precision"], summary["recall"]) == (10 / 12, 0.5)
    assert summary["seconds_per_fund"] == 20 and summary["website_accuracy"] == 0.5


def test_a_failed_test_does_not_count_as_a_result():
    rows = [_detection_row("a", "x", 8, 2, 0, 10), {**_detection_row("a", "y", 0, 0, 0, 5), "failed": "boom"}]
    summary = plot_results.summarize_detection(rows)[0]
    assert summary["failed"] == 1 and summary["correct"] == 8


def _matching_row(setting, label, decision, seconds=1.0):
    return {"setting": setting, "pair_id": "1", "kind": "unsure", "label": label, "decision": decision,
            "seconds": seconds, "usage": {"calls": 1, "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}


def test_matching_summary_counts_wrong_merges_and_what_is_left():
    rows = [_matching_row("a", "match", "match"), _matching_row("a", "no_match", "match"),
            _matching_row("a", "match", "unsure"), _matching_row("a", "no_match", "no_match")]
    summary = plot_results.summarize_matching(rows)[0]
    assert (summary["match_precision"], summary["match_recall"]) == (0.5, 0.5)
    assert (summary["wrong_merges"], summary["left_for_a_person"], summary["accuracy_when_decided"]) == (1, 0.25, 2 / 3)


def test_graphs_and_summaries_are_written(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    monkeypatch.setattr(plot_results, "RESULTS_DIR", tmp_path)
    plot_results.plot_detection(plt, [_detection_row("parser only", "x", 8, 2, 0, 1, reviewed=False),
                                      _detection_row("with model", "x", 9, 1, 0, 90, reviewed=False)])
    plot_results.plot_matching(plt, [_matching_row("rules only", "match", "unsure", 0.01),
                                     _matching_row("with model", "match", "match", 20)])
    written = {p.name for p in tmp_path.iterdir()}
    assert {"detection_quality.png", "detection_time.png", "detection_tradeoff.png", "detection_by_fund.png",
            "matching_quality.png", "matching_time.png", "matching_tradeoff.png"} <= written
    with (tmp_path / "detection_summary.csv").open() as f:
        assert [row["setting"] for row in csv.DictReader(f)] == ["parser only", "with model"]


def test_results_can_continue_after_a_stop(tmp_path):
    path = tmp_path / "r.jsonl"
    common.append_result(path, {"a": 1})
    common.append_result(path, {"a": 2})
    assert [r["a"] for r in common.read_results(path)] == [1, 2]
    assert common.read_results(tmp_path / "none.jsonl") == []




def test_correcting_labels_counts_without_running_again(tmp_path):
    fund = tmp_path / "f"
    fund.mkdir()
    (fund / "meta.json").write_text(json.dumps({"reviewed": True}))
    (fund / "gold.csv").write_text("keep,name,website,found_by,note\nyes,Puna,,,\nno,Lebanon,,,\n")
    old = {**_detection_row("a", "f", 0, 0, 0, 1, reviewed=False), "found": [{"name": "Puna"}, {"name": "Lebanon"}]}
    row = plot_results.rescore_detection([old], tmp_path)[0]
    assert (row["correct"], row["wrong"], row["reviewed"]) == (1, 1, True)


def test_changed_pair_labels_count_and_removed_ones_do_not(tmp_path):
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("pair_id,label\n1,no_match\n2,\n")
    rows = [{**_matching_row("a", "match", "match"), "pair_id": "1"}, {**_matching_row("a", "match", "match"), "pair_id": "2"}]
    assert [r["label"] for r in plot_results.relabel_matching(rows, pairs)] == ["no_match"]


def test_a_failed_test_counts_what_it_missed_and_a_retry_replaces_it(tmp_path):
    fund = tmp_path / "f"
    fund.mkdir()
    (fund / "meta.json").write_text(json.dumps({"reviewed": True}))
    (fund / "gold.csv").write_text("keep,name,website,found_by,note\nyes,Puna,,,\nyes,Belvo,,,\n")
    base = {"setting": "a", "fund": "f", "repeat": 0, "seconds": 5,
            "usage": {"calls": 1, "prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}}
    failed = {**base, "failed": "InternalServerError: 500"}
    summary = plot_results.summarize_detection(plot_results.rescore_detection([failed], tmp_path))[0]
    assert (summary["failed"], summary["correct"], summary["missed"]) == (1, 0, 2)
    retried = {**base, "found": [{"name": "Puna"}]}
    summary = plot_results.summarize_detection(plot_results.rescore_detection([failed, retried], tmp_path))[0]
    assert (summary["tests"], summary["failed"], summary["correct"], summary["missed"]) == (1, 0, 1, 1)


def test_a_company_counts_by_its_website_when_the_names_differ():
    gold = _gold(companies={"Jeeves": "https://www.tryjeeves.com/", "Other": "https://www.linkedin.com/company/x"})
    score = score_detection([{"name": "Tryjeeves", "website": "https://tryjeeves.com"},
                             {"name": "Shared", "website": "https://www.linkedin.com/company/x"}], gold)
    assert (score["correct"], score["wrong"], score["missed_names"]) == (1, 1, ["Other"])
    assert (score["websites_right"], score["websites_checked"]) == (1, 1)


def test_the_funds_name_in_front_of_a_name_does_not_count():
    gold = _gold(companies={"GOintegro": None}, fund_name="Kaszek")
    assert score_detection([{"name": "Kaszek GOintegro"}], gold)["correct"] == 1


def test_an_undecided_row_counts_by_website_too(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"reviewed": False}))
    (tmp_path / "gold.csv").write_text("keep,name,website,found_by,note\n?,Pomelo.la,https://pomelo.la/,,\n")
    gold = common.load_gold(tmp_path)
    score = score_detection([{"name": "Pomelo", "website": "https://www.pomelo.la"}, {"name": "Other"}], gold)
    assert (score["undecided"], score["wrong"]) == (1, 1)
