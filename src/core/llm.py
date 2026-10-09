"""
src/core/llm.py — Thin wrapper around the vLLM OpenAI-compatible endpoint.

vLLM is expected to be running locally, e.g.:
    docker run --gpus all -p 8000:8000 vllm/vllm-openai \
        --model mistralai/Mistral-7B-Instruct-v0.3
"""
from __future__ import annotations

from logging import getLogger
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

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

