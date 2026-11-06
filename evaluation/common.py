"""What the evaluation scripts share: settings, model overrides, results and scores.

Nothing here changes the project's code. A setting from settings.json is applied
by replacing the values in src.core.config for the length of one test, and by
wrapping the LLM client, so the code under test is the code that runs in production.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVALUATION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVALUATION_DIR.parent
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")]

from src.core import config  # noqa: E402
from merge_portfolios import _without_fund_name  # noqa: E402
from src.core.matching import company_domain, dedupe_key, same_company_name, website_domain  # noqa: E402

DATASETS = EVALUATION_DIR / "datasets"
DETECTION_DIR = DATASETS / "detection"
PAIRS_FILE = DATASETS / "matching" / "pairs.csv"
RESULTS_DIR = EVALUATION_DIR / "results"
SETTINGS_FILE = EVALUATION_DIR / "settings.json"
FUNDS_FILE = EVALUATION_DIR / "funds.json"

_CONFIG_FIELDS = {
    "model": "VLLM_MODEL", "base_url": "VLLM_BASE_URL", "api_key": "API_KEY", "temperature": "LLM_TEMPERATURE",
    "max_tokens": "LLM_MAX_TOKENS", "tool_choice": "LLM_TOOL_CHOICE",
}


def load_settings(task: str, names: list[str] | None = None) -> list[dict[str, Any]]:
    settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))[task]
    if names:
        unknown = set(names) - {s["name"] for s in settings}
        if unknown:
            sys.exit(f"Not in settings.json: {', '.join(sorted(unknown))}")
        settings = [s for s in settings if s["name"] in names]
    return settings


def uses_model(setting: dict[str, Any]) -> bool:
    return setting.get("use_model", False) or setting.get("mode", "deterministic") != "deterministic"


@contextmanager
def applied(setting: dict[str, Any]) -> Iterator[None]:
    """The setting's model, server and parameters in src.core.config, restored afterwards.
    A value the setting leaves out stays as .env has it."""
    saved = {attr: getattr(config, attr) for attr in _CONFIG_FIELDS.values()}
    for key, attr in _CONFIG_FIELDS.items():
        if key in setting:
            setattr(config, attr, setting[key])
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(config, attr, value)


@dataclass
class Usage:
    """LLM calls and tokens of one test. Reasoning tokens show where a model's time goes,
    when the server reports them."""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, response: Any) -> None:
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        details = getattr(usage, "completion_tokens_details", None)
        self.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def wrap_sync_client(client: Any, setting: dict[str, Any], usage: Usage) -> None:
    """Make every call of a synchronous OpenAI client use the setting's parameters and count it.
    make_llm_classifier sets its own temperature, so it has to be replaced here."""
    create = client.chat.completions.create

    def counted(**kwargs: Any) -> Any:
        if "temperature" in setting:
            kwargs["temperature"] = setting["temperature"]
        if "extra_body" in setting:
            kwargs["extra_body"] = setting["extra_body"]
        response = create(**kwargs)
        usage.add(response)
        return response

    client.chat.completions.create = counted


def wrap_async_client(client: Any, usage: Usage) -> None:
    """Count every call of the agent's asynchronous client."""
    create = client.chat.completions.create

    async def counted(*args: Any, **kwargs: Any) -> Any:
        response = await create(*args, **kwargs)
        usage.add(response)
        return response

    client.chat.completions.create = counted


# Results: one JSON line per test, so a long run can stop and continue

def read_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_result(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# Detection answer keys and scores

@dataclass
class Gold:
    """A fund's answer key. "?" rows aren't decided yet and count neither way."""
    fund: str
    reviewed: bool
    fund_name: str | None = None         # removed from the front of found names ("Kaszek GOintegro")
    companies: dict[str, str | None] = field(default_factory=dict)    # name -> website
    not_companies: set[str] = field(default_factory=set)
    undecided: set[str] = field(default_factory=set)


def load_gold(fund_dir: Path) -> Gold:
    meta = json.loads((fund_dir / "meta.json").read_text(encoding="utf-8"))
    gold = Gold(fund=fund_dir.name, reviewed=bool(meta.get("reviewed")), fund_name=meta.get("fund_name"))
    with (fund_dir / "gold.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            keep = (row.get("keep") or "?").strip().lower()
            if keep == "yes":
                gold.companies[row["name"]] = row.get("website") or None
            elif keep == "no":
                gold.not_companies.add(row["name"])
            else:
                gold.undecided.add(row["name"])
    return gold


def same_name(a: str, b: str) -> bool:
    return dedupe_key(a) == dedupe_key(b) or same_company_name(a, b)


def _find(name: str, names: Any) -> str | None:
    return next((n for n in names if same_name(name, n)), None)


def _find_company(company: dict[str, Any], name: str, gold: Gold) -> str | None:
    """The key's company by name, else by the company's own website: a key can use the real
    name ("Jeeves") where a run could only name it from its domain ("Tryjeeves")."""
    match = _find(name, gold.companies)
    domain = company_domain(company.get("website"))
    if match or not domain:
        return match
    return next((n for n, site in gold.companies.items() if company_domain(site) == domain), None)


def score_detection(found: list[dict[str, Any]], gold: Gold) -> dict[str, Any]:
    """Compare what a run found with the answer key. A found name that is in no row of the
    key is an error: the key lists what's on the page."""
    correct: set[str] = set()
    wrong: list[str] = []
    sites_right = sites_checked = undecided = 0
    for company in found:
        name = _without_fund_name(company["name"], gold.fund_name) or company["name"]
        match = _find_company(company, name, gold)
        if match:
            if match not in correct and gold.companies[match]:
                sites_checked += 1
                sites_right += website_domain(company.get("website")) == website_domain(gold.companies[match])
            correct.add(match)
        elif _find(name, gold.undecided):
            undecided += 1
        else:
            wrong.append(name)
    missed = sorted(set(gold.companies) - correct)
    return {
        "correct": len(correct), "wrong": len(wrong), "missed": len(missed), "undecided": undecided,
        "websites_right": sites_right, "websites_checked": sites_checked,
        "wrong_names": sorted(wrong), "missed_names": missed,
    }


def rates(correct: int, wrong: int, missed: int) -> tuple[float | None, float | None, float | None]:
    """(precision, recall, F1). Precision: how much of what was found is real. Recall: how much
    of what's real was found. None when nothing can be said (0 of 0)."""
    precision = correct / (correct + wrong) if correct + wrong else None
    recall = correct / (correct + missed) if correct + missed else None
    if not precision or not recall:
        return precision, recall, 0.0 if precision == 0 or recall == 0 else None
    return precision, recall, 2 * precision * recall / (precision + recall)
