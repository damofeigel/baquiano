"""Keeps the worker's context small by trimming page dumps it has already used.

The SDK's ToolOutputTrimmer counts user messages to decide what is "old", but a
portfolio run has a single user message, so it never trims anything. This filter
counts page-reading tool outputs instead.
"""

from __future__ import annotations

import logging
from typing import Any

from agents import RunConfig
from agents.run_config import CallModelData, ModelInputData

logger = logging.getLogger(__name__)

PAGE_TOOLS = frozenset({
    "navigate_to_url",
    "get_current_page_content",
    "get_page_content_from_url",
    "get_current_page_html",
})

TRIMMED_NOTE = (
    "[Older page content removed to save context. Companies you saved with "
    "record_companies are kept. Re-open the page if you need it again.]"
)


def trim_old_page_outputs(keep_recent: int = 2, min_chars: int = 500):
    """Build a call_model_input_filter that keeps only the newest page dumps."""

    def _filter(data: CallModelData[Any]) -> ModelInputData:
        items = data.model_data.input
        call_names = {
            item.get("call_id"): item.get("name")
            for item in items
            if isinstance(item, dict) and item.get("type") == "function_call"
        }
        page_outputs = [
            i for i, item in enumerate(items)
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and call_names.get(item.get("call_id")) in PAGE_TOOLS
        ]
        to_trim = set(page_outputs[:-keep_recent] if keep_recent else page_outputs)

        new_items: list[Any] = []
        trimmed = 0
        for i, item in enumerate(items):
            output = item.get("output") if isinstance(item, dict) else None
            if i in to_trim and isinstance(output, str) and len(output) > min_chars:
                new_items.append({**item, "output": TRIMMED_NOTE})
                trimmed += 1
            else:
                new_items.append(item)

        if trimmed:
            logger.debug("Trimmed %d old page output(s) from model input", trimmed)
        return ModelInputData(input=new_items, instructions=data.model_data.instructions)

    return _filter


def worker_run_config() -> RunConfig:
    return RunConfig(
        call_model_input_filter=trim_old_page_outputs(),
        tracing_disabled=True,
    )
