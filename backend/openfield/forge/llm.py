"""A local chat model (Ollama) for Forge layout proposals. Loopback only; JSON output is checked by the caller.

Ollama 0.40 accepts `format: "json"` but not a JSON schema, so the schema is written into the prompt and
enforced afterwards by `propose.parse_proposal`. A model that returns anything else is an error, never a
partial result.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

TIMEOUT_S = 240.0          # first call loads the model into memory; on this machine that took about 20 s
PROBE_TIMEOUT_S = 3.0
_NAME = re.compile(r"^[A-Za-z0-9._:/\-]{1,100}$")
_EMBED_WORDS = ("embed", "bge", "minilm")


class LLMError(Exception):
    """The model is unavailable or did not answer. The message is safe to show to the user."""


def _check_loopback(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise LLMError("the model server must run on this computer")
    return url.rstrip("/")


def status(url: str) -> dict:
    base = _check_loopback(url)
    try:
        r = httpx.get(f"{base}/api/tags", timeout=PROBE_TIMEOUT_S)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", []) if isinstance(m.get("name"), str)]
    except (httpx.HTTPError, ValueError, KeyError):
        return {"state": "unreachable", "url": base, "models": [], "reason": "Ollama is not running on this computer."}
    chat = [n for n in names if not any(w in n.lower() for w in _EMBED_WORDS)]
    if not chat:
        return {"state": "no_models", "url": base, "models": [], "reason": "Ollama is running but has no chat model installed."}
    return {"state": "ok", "url": base, "models": chat, "reason": None}


# Small instruct models first: a layout proposal needs quick, reliable JSON more than a large model's depth.
PREFERRED = ("qwen3:4b-instruct", "qwen3.5:4b", "gemma4:e4b", "llama3.1")


def default_model(models: list[str], chosen: str) -> str | None:
    if chosen and chosen in models:
        return chosen
    for stem in PREFERRED:
        hit = next((m for m in models if m.lower().startswith(stem) and "thinking" not in m.lower()), None)
        if hit:
            return hit
    return models[0] if models else None


def chat_json(url: str, model: str, system: str, user: str) -> dict:
    """One deterministic call. Returns the parsed JSON object, or raises LLMError."""
    base = _check_loopback(url)
    if not _NAME.match(model or ""):
        raise LLMError("the chosen model name is not valid")
    body = {"model": model, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_predict": 1200},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        r = httpx.post(f"{base}/api/chat", json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        content = r.json()["message"]["content"]
    except httpx.TimeoutException:
        raise LLMError("the model took too long to answer; try again, or choose a smaller model")
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise LLMError(f"the model did not answer ({type(e).__name__})")
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise LLMError("the model did not return valid JSON")
    if not isinstance(obj, dict):
        raise LLMError("the model did not return a JSON object")
    return obj
