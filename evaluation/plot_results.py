#!/usr/bin/env python3
"""
plot_results.py — Summaries and graphs of the evaluation results.

Usage:
    python evaluation/plot_results.py        # needs: pip install -r evaluation/requirements.txt

Writes to evaluation/results/:
    detection_summary.csv, detection_quality.png, detection_time.png, detection_tradeoff.png, detection_by_fund.png
    matching_summary.csv,  matching_quality.png,  matching_time.png,  matching_tradeoff.png

Every graph is marked DRAFT LABELS until the answer keys have been checked by a person.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from common import DETECTION_DIR, PAIRS_FILE, RESULTS_DIR, load_gold, rates, read_results, score_detection

SURFACE, INK, INK_SECONDARY, INK_MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
SERIES = "#2a78d6"
SEQUENTIAL_LIGHT, SEQUENTIAL_DARK = "#e4eefa", "#124a8c"   # one hue, light to dark
DRAFT_NOTE = "DRAFT LABELS: not checked by a person yet, so these scores can't be trusted"


# Today's answer keys: labels corrected after a run count without running it again

def latest_only(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    """The newest row per test: a failed test that ran again counts once, as its last try."""
    return list({tuple(row.get(key) for key in keys): row for row in rows}.values())


def rescore_detection(rows: list[dict[str, Any]], detection_dir: Path = DETECTION_DIR) -> list[dict[str, Any]]:
    """A failed test scores as finding nothing: leaving it out would hide what it missed."""
    updated = []
    for row in latest_only(rows, "setting", "fund", "repeat"):
        if (detection_dir / row["fund"] / "gold.csv").exists():
            gold = load_gold(detection_dir / row["fund"])
            row = {**row, **score_detection(row.get("found", []), gold), "reviewed": gold.reviewed}
        updated.append(row)
    return updated


def relabel_matching(rows: list[dict[str, Any]], pairs_file: Path = PAIRS_FILE) -> list[dict[str, Any]]:
    """The labels in the file now. A pair whose label was removed no longer counts."""
    rows = latest_only(rows, "setting", "pair_id")
    if not pairs_file.exists():
        return rows
    with pairs_file.open(encoding="utf-8", newline="") as f:
        labels = {row["pair_id"]: row["label"].strip().lower() for row in csv.DictReader(f)}
    return [{**row, "label": labels[row["pair_id"]]} for row in rows
            if labels.get(row["pair_id"]) in ("match", "no_match")]


# Summaries

def summarize_detection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per setting, adding up every fund. Times are per fund."""
    by_setting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_setting[row["setting"]].append(row)
    summary = []
    for setting, group in by_setting.items():
        ok = [r for r in group if "correct" in r]
        correct, wrong, missed = (sum(r[key] for r in ok) for key in ("correct", "wrong", "missed"))
        precision, recall, f1 = rates(correct, wrong, missed)
        checked = sum(r["websites_checked"] for r in ok)
        summary.append({
            "setting": setting, "model": group[0].get("model") or "none", "tests": len(group),
            "failed": sum("failed" in r for r in group), "correct": correct, "wrong": wrong, "missed": missed,
            "undecided": sum(r["undecided"] for r in ok), "precision": precision, "recall": recall, "f1": f1,
            "website_accuracy": sum(r["websites_right"] for r in ok) / checked if checked else None,
            "seconds_per_fund": mean(r["seconds"] for r in group),
            "llm_calls_per_fund": mean(r["usage"]["calls"] for r in group),
            "completion_tokens": sum(r["usage"]["completion_tokens"] for r in group),
            "reasoning_tokens": sum(r["usage"]["reasoning_tokens"] for r in group),
            "all_reviewed": all(r.get("reviewed") for r in group),
        })
    return summary


def summarize_matching(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Precision: of the pairs it called the same company, how many are. A wrong one joins two
    companies in the database, the costliest mistake. Unsure pairs are left for a person."""
    by_setting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_setting[row["setting"]].append(row)
    summary = []
    for setting, group in by_setting.items():
        said_match = [r for r in group if r["decision"] == "match"]
        real = [r for r in group if r["label"] == "match"]
        right_matches = sum(r["label"] == "match" for r in said_match)
        decided = [r for r in group if r["decision"] != "unsure"]
        summary.append({
            "setting": setting, "model": group[0].get("model") or "none", "pairs": len(group),
            "failed": sum("failed" in r for r in group),
            "match_precision": right_matches / len(said_match) if said_match else None,
            "match_recall": right_matches / len(real) if real else None,
            "accuracy_when_decided": (sum(r["decision"] == r["label"] for r in decided) / len(decided)
                                      if decided else None),
            "left_for_a_person": sum(r["decision"] == "unsure" for r in group) / len(group),
            "wrong_merges": len(said_match) - right_matches,
            "seconds_per_pair": mean(r["seconds"] for r in group),
            "llm_calls": sum(r["usage"]["calls"] for r in group),
            "completion_tokens": sum(r["usage"]["completion_tokens"] for r in group),
            "reasoning_tokens": sum(r["usage"]["reasoning_tokens"] for r in group),
        })
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()} for row in rows)


# Graphs

def _figure(plt: Any, width: float, height: float, columns: int = 1) -> tuple[Any, list[Any]]:
    figure, axes = plt.subplots(1, columns, figsize=(width, height), sharey=True, facecolor=SURFACE, squeeze=False)
    axes = list(axes[0])
    for ax in axes:
        ax.set_facecolor(SURFACE)
        ax.tick_params(colors=INK_SECONDARY, length=0, labelsize=9)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    return figure, axes


def _finish(plt: Any, figure: Any, path: Path, title: str, draft: bool) -> None:
    figure.suptitle(title, x=0.01, ha="left", color=INK, fontsize=12, fontweight="bold")
    figure.tight_layout(rect=(0, 0.05 if draft else 0, 1, 0.95))
    if draft:
        figure.text(0.01, 0.015, DRAFT_NOTE, color=INK_SECONDARY, fontsize=8.5)
    figure.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(figure)


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "%":
        return f"{value * 100:.0f}%"
    if unit == "s":
        if value < 0.1:
            return "<0.1 s"
        return f"{value:.0f} s" if value >= 10 else f"{value:.1f} s"
    return f"{value:.0f}"


def bars(plt: Any, path: Path, title: str, labels: list[str], panels: list[tuple[str, list[float | None], str]],
         draft: bool) -> None:
    """Horizontal bars, one panel per measure: each has its own scale, so no double axis."""
    height = max(2.2, 0.48 * len(labels) + 1.3)
    figure, axes = _figure(plt, 3.1 * len(panels) + 2.4, height, len(panels))
    positions = list(range(len(labels)))
    for ax, (name, values, unit) in zip(axes, panels):
        numbers = [v or 0 for v in values]
        ax.barh(positions, numbers, height=0.56, color=SERIES)
        top = 1.0 if unit == "%" else (max(numbers) or 1)
        ax.set_xlim(0, top * 1.22)
        for y, value, number in zip(positions, values, numbers):
            ax.text(number + top * 0.02, y, _format(value, unit), va="center", color=INK, fontsize=9)
        ax.set_title(name, loc="left", color=INK_SECONDARY, fontsize=10)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_xticklabels([])
        ax.spines["bottom"].set_visible(False)
    axes[0].set_yticks(positions, labels)
    axes[0].invert_yaxis()
    _finish(plt, figure, path, title, draft)


def _label_points(figure: Any, ax: Any, points: list[tuple[str, float, float]]) -> None:
    """Name each point directly, at the first spot around it that covers no other name or point."""
    spots = [(8, 6, "left"), (8, -14, "left"), (-8, 6, "right"), (-8, -14, "right"),
             (8, 20, "left"), (8, -28, "left"), (-8, 20, "right"), (-8, -28, "right")]
    renderer = figure.canvas.get_renderer()
    placed: list[Any] = []
    markers = [ax.transData.transform((x, y)) for _, x, y in points]
    for name, x, y in sorted(points, key=lambda p: (p[1], p[2])):
        for dx, dy, side in spots:
            label = ax.annotate(name, (x, y), xytext=(dx, dy), textcoords="offset points", ha=side, color=INK, fontsize=9)
            box = label.get_window_extent(renderer)
            if not any(box.overlaps(other) for other in placed) and not any(box.contains(*m) for m in markers):
                break
            label.remove()
        else:
            label = ax.annotate(name, (x, y), xytext=spots[0][:2], textcoords="offset points", color=INK, fontsize=9)
            box = label.get_window_extent(renderer)
        placed.append(box)


def tradeoff(plt: Any, path: Path, title: str, points: list[tuple[str, float, float | None]], x_label: str,
             y_label: str, draft: bool) -> None:
    """Time against quality: the setting to look for is top left."""
    figure, (ax,) = _figure(plt, 8.2, 4.8)
    shown = [(name, x, y) for name, x, y in points if y is not None]
    ax.scatter([x for _, x, _ in shown], [y for _, _, y in shown], s=70, color=SERIES, edgecolor=SURFACE, linewidth=2,
               zorder=3)
    right = max((x for _, x, _ in shown), default=1) or 1
    ax.set_xlim(-right * 0.04, right * 1.4)
    low = min((y for _, _, y in shown), default=0)
    ax.set_ylim(max(0, low - 0.12), 1.04)
    _label_points(figure, ax, shown)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.set_xlabel(x_label, color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel(y_label, color=INK_SECONDARY, fontsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(GRID)
    _finish(plt, figure, path, title, draft)


def by_fund(plt: Any, path: Path, rows: list[dict[str, Any]], draft: bool) -> None:
    """F1 per fund and setting: which funds need the model, and which don't."""
    from matplotlib.colors import LinearSegmentedColormap

    settings = list(dict.fromkeys(r["setting"] for r in rows))
    funds = sorted({r["fund"] for r in rows})
    grid: list[list[float | None]] = []
    for fund in funds:
        line = []
        for setting in settings:
            group = [r for r in rows if r["fund"] == fund and r["setting"] == setting and "correct" in r]
            f1 = rates(*(sum(r[key] for r in group) for key in ("correct", "wrong", "missed")))[2] if group else None
            line.append(f1)
        grid.append(line)
    figure, (ax,) = _figure(plt, 1.7 * len(settings) + 2.6, 0.42 * len(funds) + 1.9)
    colors = LinearSegmentedColormap.from_list("blue", [SEQUENTIAL_LIGHT, SEQUENTIAL_DARK])
    ax.imshow([[v if v is not None else float("nan") for v in line] for line in grid], cmap=colors, vmin=0, vmax=1,
              aspect="auto")
    for y, line in enumerate(grid):
        for x, value in enumerate(line):
            dark_cell = value is not None and value > 0.55
            ax.text(x, y, _format(value, "%"), ha="center", va="center", fontsize=9,
                    color=SURFACE if dark_cell else INK)
    ax.set_xticks(range(len(settings)), [s.replace(", ", ",\n") for s in settings], fontsize=8.5)
    ax.set_yticks(range(len(funds)), funds)
    ax.xaxis.tick_top()
    ax.spines["bottom"].set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(settings))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(funds))], minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    _finish(plt, figure, path, "Detection quality (F1) by fund", draft)


def plot_detection(plt: Any, rows: list[dict[str, Any]]) -> None:
    summary = summarize_detection(rows)
    write_csv(RESULTS_DIR / "detection_summary.csv", summary)
    labels = [s["setting"] for s in summary]
    draft = not all(s["all_reviewed"] for s in summary)
    bars(plt, RESULTS_DIR / "detection_quality.png", "Detection quality", labels, [
        ("Precision: what it found is real", [s["precision"] for s in summary], "%"),
        ("Recall: what's real was found", [s["recall"] for s in summary], "%"),
        ("F1: both together", [s["f1"] for s in summary], "%"),
    ], draft)
    bars(plt, RESULTS_DIR / "detection_time.png", "Detection cost per fund", labels, [
        ("Time per fund", [s["seconds_per_fund"] for s in summary], "s"),
        ("Model calls per fund", [s["llm_calls_per_fund"] for s in summary], "n"),
    ], draft)
    tradeoff(plt, RESULTS_DIR / "detection_tradeoff.png", "Detection: quality against time",
             [(s["setting"], s["seconds_per_fund"], s["f1"]) for s in summary],
             "Seconds per fund", "F1", draft)
    by_fund(plt, RESULTS_DIR / "detection_by_fund.png", rows, draft)


def plot_matching(plt: Any, rows: list[dict[str, Any]]) -> None:
    summary = summarize_matching(rows)
    write_csv(RESULTS_DIR / "matching_summary.csv", summary)
    labels = [s["setting"] for s in summary]
    bars(plt, RESULTS_DIR / "matching_quality.png", "Matching quality", labels, [
        ("Precision: its matches are real", [s["match_precision"] for s in summary], "%"),
        ("Recall: real matches it found", [s["match_recall"] for s in summary], "%"),
        ("Left for a person", [s["left_for_a_person"] for s in summary], "%"),
    ], False)
    bars(plt, RESULTS_DIR / "matching_time.png", "Matching cost and worst mistakes", labels, [
        ("Time per pair", [s["seconds_per_pair"] for s in summary], "s"),
        ("Wrong merges", [s["wrong_merges"] for s in summary], "n"),
    ], False)
    tradeoff(plt, RESULTS_DIR / "matching_tradeoff.png", "Matching: pairs answered correctly against time",
             [(s["setting"], s["seconds_per_pair"],
               None if s["accuracy_when_decided"] is None
               else s["accuracy_when_decided"] * (1 - s["left_for_a_person"])) for s in summary],
             "Seconds per pair", "Pairs answered correctly", False)


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    detection = rescore_detection(read_results(RESULTS_DIR / "detection.jsonl"))
    matching = relabel_matching(read_results(RESULTS_DIR / "matching.jsonl"))
    if not detection and not matching:
        raise SystemExit("No results yet. Run run_detection.py or run_matching.py first.")
    if detection:
        plot_detection(plt, detection)
    if matching:
        plot_matching(plt, matching)
    print(f"Graphs and summaries in {RESULTS_DIR}")


if __name__ == "__main__":
    main()
