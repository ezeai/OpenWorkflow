"""The node-list cache: an expired list is served at once and refreshed in the background."""
import threading
import time

import pytest

from openfield import api_forge
from openfield import app as appmod
from openfield.forge import object_info as oi


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setitem(api_forge._INFO, "info", None)
    monkeypatch.setitem(api_forge._INFO, "refreshing", False)
    monkeypatch.setitem(api_forge._INFO, "url", None)
    return appmod.create_app(tmp_path)


def _wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_expired_list_is_served_immediately_and_refreshed_in_the_background(app, monkeypatch):
    first, second = object(), object()
    calls = []
    release = threading.Event()

    def fake_load(home, client_get=None):
        calls.append(1)
        if len(calls) == 1:
            return first
        release.wait(5)          # a slow ComfyUI: the refresh does not finish until the test allows it
        return second
    monkeypatch.setattr(oi, "load", fake_load)

    assert api_forge.info_for_app(app) is first          # first read blocks and is the only one so far
    assert len(calls) == 1

    api_forge._INFO["at"] = -1e9                         # make the cached list expired
    started = time.monotonic()
    got = api_forge.info_for_app(app)
    assert got is first, "an expired list is served at once"
    assert time.monotonic() - started < 1.0, "the caller does not wait for ComfyUI"

    release.set()
    assert _wait_for(lambda: api_forge._INFO["info"] is second), "the list is refreshed in the background"
    assert api_forge.info_for_app(app) is second
    assert len(calls) == 2


def test_only_one_background_refresh_runs_at_a_time(app, monkeypatch):
    release = threading.Event()
    calls = []

    def fake_load(home, client_get=None):
        calls.append(1)
        if len(calls) > 1:
            release.wait(5)
        return object()
    monkeypatch.setattr(oi, "load", fake_load)
    api_forge.info_for_app(app)
    api_forge._INFO["at"] = -1e9
    api_forge.info_for_app(app)
    api_forge._INFO["at"] = -1e9
    api_forge.info_for_app(app)
    release.set()
    assert _wait_for(lambda: not api_forge._INFO["refreshing"])
    assert len(calls) == 2, "a second refresh is not started while one is running"


def test_a_failed_refresh_keeps_the_last_list_and_allows_a_retry(app, monkeypatch):
    good = object()
    attempts = []

    def fake_load(home, client_get=None):
        attempts.append(1)
        if len(attempts) == 2:
            raise OSError("ComfyUI went away")
        return good
    monkeypatch.setattr(oi, "load", fake_load)
    api_forge.info_for_app(app)
    api_forge._INFO["at"] = -1e9
    assert api_forge.info_for_app(app) is good
    assert _wait_for(lambda: not api_forge._INFO["refreshing"])
    assert api_forge._INFO["info"] is good, "the last good list is kept"
    api_forge._INFO["at"] = -1e9
    api_forge.info_for_app(app)
    assert _wait_for(lambda: len(attempts) == 3)
