"""Refuse requests that reach the server through a non-loopback address.

Open Workflow is local-only. The launchers bind 127.0.0.1, but a manual `--host 0.0.0.0` would expose the
API to the network, so every request is checked against the address it arrived on. uvicorn reports that
address as the ASGI `server` entry; a hostname there (such as the test client's "testserver") is not checked.
"""
from __future__ import annotations

import ipaddress

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

REFUSAL = "Open Workflow accepts connections on loopback (127.0.0.1) only."


def is_loopback_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return True  # not an IP literal: nothing to judge
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


class LoopbackOnly:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not is_loopback_address((scope.get("server") or ("", 0))[0]):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": REFUSAL})
            else:
                await PlainTextResponse(REFUSAL, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
