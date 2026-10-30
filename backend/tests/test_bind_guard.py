import asyncio

import pytest
from starlette.responses import PlainTextResponse

from openfield.bind_guard import LoopbackOnly, is_loopback_address


async def _inner(scope, receive, send):
    await PlainTextResponse("ok")(scope, receive, send)


def _run(scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(LoopbackOnly(_inner)(scope, receive, send))
    return messages


def _http(server):
    return {"type": "http", "method": "GET", "path": "/api/health", "headers": [], "server": server}


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1", "localhost", "testserver", "::1%lo"])
def test_loopback_and_non_ip_hosts_pass(host):
    assert is_loopback_address(host)
    assert _run(_http((host, 8765)))[0]["status"] == 200


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.5", "0.0.0.0", "::", "2001:db8::1", "::ffff:192.168.1.20"])
def test_non_loopback_addresses_are_refused(host):
    assert not is_loopback_address(host)
    start = _run(_http((host, 8765)))[0]
    assert start["status"] == 403


def test_refused_websocket_is_closed_with_policy_code():
    scope = {"type": "websocket", "path": "/ws", "headers": [], "server": ("192.168.1.20", 8765)}
    messages = _run(scope)
    assert messages == [{"type": "websocket.close", "code": 1008, "reason": messages[0]["reason"]}]


def test_lifespan_is_not_checked():
    scope = {"type": "lifespan", "server": ("0.0.0.0", 8765)}
    messages = []

    async def inner(scope, receive, send):
        messages.append("inner")

    async def receive():
        return {}

    async def send(message):
        pass

    asyncio.run(LoopbackOnly(inner)(scope, receive, send))
    assert messages == ["inner"]
