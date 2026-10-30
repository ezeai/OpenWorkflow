"""The Forge operator: a local chat model that reads your apps and proposes runs. It never runs anything itself.

Protocol: every model reply must be one JSON object, either a tool call {"tool": name, "args": {...}} or a final
answer {"answer": "..."}. Replies are parsed strictly. A reply that is not one of those is sent back once with a
correction, then the turn fails. This works the same on every local server, because no server needs tool support.

Tools are read-only, except `propose_run`, which only adds a card. A card runs when the user clicks Run, or when
autopilot is on, through the same run route the Run button uses. Nothing here touches ComfyUI, the files, or any
cloud provider.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

MAX_STEPS = 4
MAX_HISTORY = 12
MAX_MESSAGE = 2000
_NUMERIC = {"number", "slider"}
_CLAIMS_CARD = re.compile(r"propos|click (the )?run|run card|\bcard\b|\bi (have )?(started|launched|ran)\b|has been (started|run)", re.IGNORECASE)


class OperatorError(Exception):
    """The turn could not be completed. The message is safe to show to the user."""


class AppsView(Protocol):
    def list_apps(self) -> list[dict]: ...
    def get_app(self, app_id: str) -> dict | None: ...


TOOLS: dict[str, dict] = {
    "list_apps": {"about": "List the apps: id, name and readiness (ready, needs_attention, blocked).", "args": {}},
    "get_app": {"about": "One app's controls: id, type, label, default and options. Use the control ids as values.",
                "args": {"app_id": "string"}},
    "propose_run": {"about": "Propose running an app with these values. Shows a Run card; nothing runs until the user clicks Run.",
                    "args": {"app_id": "string", "values": "object: control id to value"}},
}


def _system_prompt() -> str:
    lines = [
        "You are the OW agent inside Open Workflow, a local app for running ComfyUI workflows as apps.",
        "You help the user find an app and run it. You cannot run anything yourself: use propose_run and the user clicks Run.",
        "Reply with exactly ONE JSON object and nothing else:",
        '  to use a tool:  {"tool": "<name>", "args": { ... }}',
        '  to answer:      {"answer": "<short plain-language reply>"}',
        "Tools:",
    ]
    for name, t in TOOLS.items():
        lines.append(f"- {name}: {t['about']} args: {json.dumps(t['args'])}")
    lines.append("Never invent app ids or control ids. Look them up with list_apps and get_app first.")
    lines.append("Every value the user gives (a prompt, a size, a seed, a choice) goes into propose_run values, under its control id.")
    lines.append("If a value the user asked for has no matching control, say so instead of claiming it was used.")
    return "\n".join(lines)


def parse_action(text: str) -> dict | None:
    """The one JSON object in a reply, or None. Accepts a reply wrapped in prose or a code fence."""
    decoder = json.JSONDecoder()
    stripped = text.strip().strip("`").strip()
    if stripped.startswith("json"):
        stripped = stripped[4:].strip()
    for start in (i for i, ch in enumerate(stripped) if ch == "{"):
        try:
            obj, _ = decoder.raw_decode(stripped[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (isinstance(obj.get("answer"), str) or
                                      (isinstance(obj.get("tool"), str) and isinstance(obj.get("args", {}), dict))):
            obj.setdefault("args", {})
            return obj
    return None


def _by_name(name: str, controls: list[dict]) -> dict | None:
    """A control named by its label, its id or its type, but only when exactly one control matches."""
    wanted = name.strip().lower()
    hits = [c for c in controls if wanted in (str(c.get("label", "")).lower(), str(c.get("id", "")).lower(), str(c.get("type", "")).lower())]
    return hits[0] if len(hits) == 1 else None


def _coerce(control: dict, value: Any) -> Any:
    kind = control.get("type")
    if kind == "toggle":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("expects true or false")
    if kind in _NUMERIC or kind == "seed":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError("expects a number")
        return int(num) if kind == "seed" else num
    if kind == "select":
        options = [str(o) for o in control.get("options") or []]
        if str(value) not in options:
            raise ValueError(f"must be one of {', '.join(options) or 'the listed options'}")
        return str(value)
    if kind in ("text", "prompt", "image_upload"):
        if kind == "image_upload":
            raise ValueError("needs an image from the user; the operator cannot attach one")
        return str(value)[:4000]
    raise ValueError("this control cannot be set by the operator")


class Operator:
    def __init__(self, apps: AppsView, chat: Callable[[list[dict]], str], max_steps: int = MAX_STEPS):
        self.apps = apps
        self.chat = chat
        self.max_steps = max_steps

    # ---- tools (read only, plus propose_run which adds a card)

    def _tool(self, name: str, args: dict, cards: list[dict]) -> dict:
        if name == "list_apps":
            return {"apps": self.apps.list_apps()}
        if name == "get_app":
            app = self.apps.get_app(str(args.get("app_id", "")))
            return {"app": app} if app else {"error": "no app with that id"}
        if name == "propose_run":
            return self._propose(args, cards)
        return {"error": f"unknown tool '{name}'"}

    def _propose(self, args: dict, cards: list[dict]) -> dict:
        app_id = str(args.get("app_id", ""))
        app = self.apps.get_app(app_id)
        if not app:
            return {"error": "no app with that id"}
        if app.get("readiness") == "blocked":
            return {"error": f"'{app['name']}' is blocked: {'; '.join(app.get('reasons') or []) or 'it cannot run yet'}"}
        raw = args.get("values") if isinstance(args.get("values"), dict) else {}
        controls = {c["id"]: c for c in app.get("components", [])}
        values, rejected = {}, []
        for key, value in raw.items():
            control = controls.get(key) or _by_name(str(key), list(controls.values()))
            if control is None:
                rejected.append(f"{key}: not a control of this app")
                continue
            try:
                values[control["id"]] = _coerce(control, value)
            except ValueError as e:
                rejected.append(f"{control.get('label') or key}: {e}")
        card = {"kind": "run_app", "app_id": app_id, "app_name": app["name"], "values": values, "rejected": rejected}
        cards.append(card)
        return {"ok": True, "card": len(cards), "accepted": sorted(values), "rejected": rejected}

    # ---- the turn

    def turn(self, message: str, history: list[dict]) -> dict:
        message = (message or "").strip()
        if not message:
            raise OperatorError("write a message first")
        if len(message) > MAX_MESSAGE:
            raise OperatorError(f"the message is longer than {MAX_MESSAGE} characters")
        messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
        for turn in history[-MAX_HISTORY:]:
            role = turn.get("role")
            content = str(turn.get("content", ""))[:MAX_MESSAGE]
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        cards: list[dict] = []
        trace: list[dict] = []
        corrected = False
        for _ in range(self.max_steps):
            action = self._next_action(messages)
            if "answer" in action:
                answer = action["answer"].strip()
                if not cards and _CLAIMS_CARD.search(answer):
                    # A model sometimes says it proposed a run without calling propose_run. Say so once, then
                    # never pass a false claim to the user.
                    if not corrected:
                        corrected = True
                        messages.append({"role": "assistant", "content": json.dumps({"answer": answer})[:2000]})
                        messages.append({"role": "user", "content": (
                            "Check: no run card was created, because no propose_run call succeeded. If the user asked for a run, "
                            "call propose_run now with the app id and values. Otherwise answer without claiming a card.")})
                        continue
                    answer = ("I did not create a run card, so nothing is ready to run. "
                              "Tell me which app and values to use, or ask again.")
                return {"answer": answer[:4000], "cards": cards, "trace": trace}
            name, args = action["tool"], action["args"]
            result = self._tool(name, args, cards)
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "assistant", "content": json.dumps({"tool": name, "args": args})})
            messages.append({"role": "user", "content": "Tool result: " + json.dumps(result)[:4000]})
        if cards:
            return {"answer": f"I proposed {len(cards)} run card{'s' if len(cards) != 1 else ''} below. Click Run on a card to start it.",
                    "cards": cards, "trace": trace}
        return {"answer": "I stopped after several steps without a final answer. Ask again with a smaller request.",
                "cards": cards, "trace": trace}

    def _next_action(self, messages: list[dict]) -> dict:
        reply = self.chat(messages)
        action = parse_action(reply)
        if action is None:
            messages.append({"role": "assistant", "content": reply[:2000]})
            messages.append({"role": "user", "content": 'That was not one JSON object of the form {"tool": ...} or {"answer": ...}. Reply with only that.'})
            reply = self.chat(messages)
            action = parse_action(reply)
            if action is None:
                raise OperatorError("the model did not return a usable reply; try again or choose another model")
        return action
