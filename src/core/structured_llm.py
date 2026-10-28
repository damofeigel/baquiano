"""One structured LLM call: JSON-schema output from the vLLM server, and checks on it.

Used by scripts that ask the model for a single answer (enrich_startups.py,
the entity-resolution judge), as opposed to the browsing agents in llm.py.
The OpenAI client is passed in, so importing this needs no openai package.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable

from src.core import config

Classifier = Callable[[list[dict[str, str]], dict[str, Any]], dict[str, Any]]


def check_llm(client: Any, model: str) -> str | None:
    """None if the server answers and serves the model, else a message saying what's wrong."""
    try:
        served = [m.id for m in client.models.list()]
    except Exception as exc:
        return (f"Can't reach the LLM server at {config.VLLM_BASE_URL} ({type(exc).__name__}: {exc}). "
                f"Is vLLM running? Check with: curl {config.VLLM_BASE_URL.rstrip('/')}/models")
    if served and model not in served:
        return f"The server doesn't serve VLLM_MODEL={model!r}. It serves: {', '.join(served)}"
    return None


def make_llm_classifier(client: Any, model: str, name: str = "startup_enrichment") -> Classifier:
    """Structured output via the server's JSON-schema mode, with a plain-JSON fallback."""
    def classify(messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=config.LLM_MAX_TOKENS,
            response_format={"type": "json_schema",
                             "json_schema": {"name": name, "schema": schema, "strict": True}},
        )
        return parse_json(response.choices[0].message.content or "")
    return classify


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in a reply, tolerating code fences or text around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"no JSON object in model reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def quote_found(quote: str | None, texts: list[str]) -> bool:
    """True if the quoted evidence really appears in the texts (case, accents, spacing ignored)."""
    if not quote:
        return False
    q = _norm(quote).strip(" .\"'“”…")
    return len(q) >= 4 and q in _norm("\n".join(texts))
