"""Local chat models for the Forge operator: Ollama's own API, and any OpenAI-compatible local server.

Presets cover the common local runtimes (Ollama, LM Studio, llama.cpp's llama-server, KoboldCpp, Jan, vLLM and
text-generation-webui). A custom loopback address works for anything else that speaks /v1/chat/completions.
Every endpoint must be on this computer; a remote host is refused before any request is made.

The operator only needs plain text back. Replies are not forced into JSON mode because not every server supports it;
the caller parses and checks the reply itself.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

PROBE_TIMEOUT_S = 1.5
CHAT_TIMEOUT_S = 240.0          # a first call may load the model into memory
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class ProviderError(Exception):
    """The endpoint is unavailable or refused the request. The message is safe to show to the user."""


@dataclass(frozen=True)
class Endpoint:
    id: str
    label: str
    base: str        # without a trailing slash; OpenAI-style servers include /v1
    kind: str        # "ollama" or "openai"


PRESETS: tuple[Endpoint, ...] = (
    Endpoint("ollama", "Ollama", "http://127.0.0.1:11434", "ollama"),
    Endpoint("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1", "openai"),
    Endpoint("llamacpp", "llama.cpp (llama-server)", "http://127.0.0.1:8080/v1", "openai"),
    Endpoint("koboldcpp", "KoboldCpp", "http://127.0.0.1:5001/v1", "openai"),
    Endpoint("jan", "Jan", "http://127.0.0.1:1337/v1", "openai"),
    Endpoint("vllm", "vLLM", "http://127.0.0.1:8000/v1", "openai"),
    Endpoint("textgen", "text-generation-webui", "http://127.0.0.1:5000/v1", "openai"),
)


def check_loopback(base: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "") not in LOOPBACK:
        raise ProviderError("the model server must run on this computer")
    return base.rstrip("/")


def custom(base: str, kind: str) -> Endpoint:
    if kind not in ("ollama", "openai"):
        raise ProviderError("the server type must be 'ollama' or 'openai'")
    base = check_loopback(base)
    return Endpoint("custom", "Custom server", base, kind)


def _names(ep: Endpoint) -> list[str]:
    if ep.kind == "ollama":
        r = httpx.get(f"{ep.base}/api/tags", timeout=PROBE_TIMEOUT_S)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", []) if isinstance(m.get("name"), str)]
    r = httpx.get(f"{ep.base}/models", timeout=PROBE_TIMEOUT_S)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", []) if isinstance(m.get("id"), str)]


def _chat_names(names: list[str]) -> list[str]:
    return [n for n in names if not any(w in n.lower() for w in ("embed", "bge", "minilm", "rerank"))]


def status(ep: Endpoint) -> dict:
    """Probe one endpoint. Never raises: 'unreachable' is an answer."""
    try:
        models = _chat_names(_names(ep))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"id": ep.id, "label": ep.label, "base": ep.base, "kind": ep.kind, "state": "unreachable",
                "models": [], "reason": f"{ep.label} is not running on {urlparse(ep.base).netloc}."}
    if not models:
        return {"id": ep.id, "label": ep.label, "base": ep.base, "kind": ep.kind, "state": "no_models",
                "models": [], "reason": f"{ep.label} is running but has no chat model loaded."}
    return {"id": ep.id, "label": ep.label, "base": ep.base, "kind": ep.kind, "state": "ok", "models": models, "reason": None}


def probe_all() -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(PRESETS)) as pool:
        return list(pool.map(status, PRESETS))


def chat(ep: Endpoint, model: str, messages: list[dict], timeout: float = CHAT_TIMEOUT_S) -> str:
    """One non-streaming chat turn. Returns the reply text with any reasoning block removed."""
    if not model or len(model) > 200:
        raise ProviderError("choose a model first")
    try:
        if ep.kind == "ollama":
            payload = {"model": model, "messages": messages, "stream": False, "think": False,
                       "options": {"temperature": 0.2, "num_ctx": 8192}}
            r = httpx.post(f"{ep.base}/api/chat", json=payload, timeout=timeout)
            if r.status_code == 400 and "think" in r.text.lower():   # this model does not take the think switch
                del payload["think"]
                r = httpx.post(f"{ep.base}/api/chat", json=payload, timeout=timeout)
            r.raise_for_status()
            text = r.json()["message"]["content"]
        else:
            payload = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 1024, "stream": False}
            r = httpx.post(f"{ep.base}/chat/completions", json=payload, timeout=timeout)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as e:
        raise ProviderError(f"{ep.label} did not answer ({type(e).__name__})") from e
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ProviderError(f"{ep.label} returned a reply in a shape this app does not read") from e
    if not isinstance(text, str):
        raise ProviderError(f"{ep.label} returned no text")
    return _THINK.sub("", text).strip()
