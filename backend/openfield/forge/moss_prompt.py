"""Prompt helper for MOSS posters: a short idea becomes one detailed image prompt, written by the MOSS text encoder.

The encoder that draws the poster also writes the prompt (ComfyUI core TextGenerate on the MOSS server), so no
second model is needed. The answer is checked before it is used. If the MOSS server is not running, or the answer
is not a usable prompt, a plain template is used instead and the result says so. Nothing leaves this computer.
"""
from __future__ import annotations

import httpx

from . import llm, moss_enhancer, moss_graph
from .moss_worker import MossWorker, WorkerError

MIN_CHARS, MAX_CHARS = 20, 1400
MAX_TOKENS = 400
SEED = 42


def template(idea: str) -> str:
    base = " ".join(idea.split()).rstrip(".")
    return f"{base}, professional product advertisement, clean composition, soft studio light, sharp focus"


def _usable(text: str | None) -> str | None:
    if not text:
        return None
    p = " ".join(text.split()).strip().strip('"')
    if not (MIN_CHARS <= len(p) <= MAX_CHARS):
        return None
    return p


def fallback(idea: str, note: str) -> dict:
    return {"state": "done", "prompt": template(idea), "source": "template", "note": f"{note} Used the plain template instead."}


def start(idea: str, worker: MossWorker, client_id: str) -> dict:
    """Start the MOSS text job. Returns {"state": "running", "prompt_id"}, or a finished template result if MOSS is down."""
    idea = " ".join((idea or "").split())
    if not idea:
        return {"state": "done", "prompt": "", "source": "none", "note": "Write a short idea first."}
    if worker.status()["state"] != "ready":
        return fallback(idea, "The MOSS server is not running, so the prompt helper cannot use its text encoder.")
    try:
        graph = moss_graph.build_text(idea, MAX_TOKENS, system_prompt=moss_enhancer.FORMULA, seed=SEED)
        return {"state": "running", "prompt_id": worker.submit(graph, client_id=client_id), "idea": idea}
    except (WorkerError, moss_graph.GraphError) as e:
        return fallback(idea, str(e))


def finish(job: dict, worker: MossWorker) -> dict | None:
    """None while the text is still being written. Otherwise the finished result."""
    res = worker.text_result(job["prompt_id"])
    if res is None:
        return None
    if not res["ok"]:
        return fallback(job["idea"], "The MOSS text encoder did not return text.")
    p = _usable(res["text"])
    if p is None:
        return fallback(job["idea"], "The text encoder's answer was not a usable prompt.")
    return {"state": "done", "prompt": p, "source": "moss_text_encoder", "note": None}


FAST_TIMEOUT_S = 120.0


def fast(idea: str, llm_url: str, llm_model: str) -> dict:
    """The fast helper: a small local chat model with the same enhancer system prompt, unloaded straight after.

    Returns the same shape as finish(). Never raises for a model problem: the template is the fallback.
    """
    idea = " ".join((idea or "").split())
    if not idea:
        return {"state": "done", "prompt": "", "source": "none", "note": "Write a short idea first."}
    try:
        st = llm.status(llm_url)
        if st["state"] != "ok":
            return fallback(idea, f"{st['reason']}")
        model = llm.default_model(st["models"], llm_model)
        base = llm._check_loopback(llm_url)
        body = {"model": model, "stream": False, "think": False, "keep_alive": 0,
                "options": {"temperature": moss_enhancer.SETTINGS["temperature"], "top_k": moss_enhancer.SETTINGS["top_k"],
                            "top_p": moss_enhancer.SETTINGS["top_p"], "min_p": moss_enhancer.SETTINGS["min_p"],
                            "num_predict": MAX_TOKENS},
                "messages": [{"role": "system", "content": moss_enhancer.FORMULA}, {"role": "user", "content": idea}]}
        r = httpx.post(f"{base}/api/chat", json=body, timeout=FAST_TIMEOUT_S)
        r.raise_for_status()
        text = r.json()["message"]["content"]
    except (llm.LLMError, httpx.HTTPError, KeyError, ValueError) as e:
        return fallback(idea, f"The local chat model did not answer ({type(e).__name__})")
    p = _usable(text)
    if p is None:
        return fallback(idea, "The local model's answer was not a usable prompt.")
    return {"state": "done", "prompt": p, "source": "local_model", "note": None, "model": model}
