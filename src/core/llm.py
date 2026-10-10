"""
src/core/llm.py — Thin wrapper around the vLLM OpenAI-compatible endpoint.

vLLM is expected to be running locally, e.g.:
    docker run --gpus all -p 8000:8000 vllm/vllm-openai \
        --model mistralai/Mistral-7B-Instruct-v0.3
"""
from __future__ import annotations

import dataclasses
from logging import getLogger
from typing import Any

from openai import AsyncOpenAI
from agents import Model, ModelResponse, OpenAIChatCompletionsModel

from src.core import config

logger = getLogger(__name__)
logger.setLevel(config.LOG_LEVEL)

_client: AsyncOpenAI | None = None
_model: OpenAIChatCompletionsModel | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        try:
            _client = AsyncOpenAI(
                base_url=config.VLLM_BASE_URL,
                api_key=config.API_KEY,
            )
        except Exception as exc:
            logger.error(f"Failed to initialize LLM client: {exc}")
            raise
    return _client


def get_model() -> OpenAIChatCompletionsModel:
    global _model
    if _model is None:
        _model = OpenAIChatCompletionsModel(
            model=config.VLLM_MODEL,
            openai_client=get_client(),
        )
    return _model


class OneToolCallPerTurn(Model):
    """Wraps a model so each response carries at most one tool call.

    Small models (Llama 3.1 8B) often emit their whole plan as parallel tool
    calls in one response: navigate, read, record, finish. The SDK runs them
    all at once, so record/finish execute before any page has been read.
    Keeping only the first call forces the model to see each result before
    choosing its next step.
    """

    def __init__(self, inner: Model) -> None:
        self.inner = inner

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        response = await self.inner.get_response(*args, **kwargs)
        output, seen_call, dropped = [], False, []
        for item in response.output:
            if getattr(item, "type", None) == "function_call":
                if seen_call:
                    dropped.append(getattr(item, "name", type(item).__name__))
                    continue
                seen_call = True
            output.append(item)
        if dropped:
            logger.warning("Dropped %d extra tool call(s) from one response: %s", len(dropped), dropped)
            response = dataclasses.replace(response, output=output)
        return response

    def stream_response(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("Streaming is not used by this pipeline")
