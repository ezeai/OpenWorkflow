"""MOSS poster routes, with a fake MOSS server: submit, wait, save the image and receipt, refuse what cannot run."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import api_moss, app as appmod
from openfield.forge.moss_worker import WorkerError


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeWorker:
    """Stands in for the MOSS server. Set `done` to decide what /history reports."""
    submitted = []
    done = None          # None = still running; dict = finished
    refuse = None        # message to raise on submit
    base = "http://127.0.0.1:8189"

    def __init__(self, url=None):
        self.base = self.base

    def status(self):
        return {"state": "ready", "url": self.base, "message": "ok"}

    def submit(self, graph, client_id):
        if FakeWorker.refuse:
            raise WorkerError(FakeWorker.refuse)
        FakeWorker.submitted.append(graph)
        return "prompt-1"

    def history(self, prompt_id):
        return FakeWorker.done

    def fetch(self, image):
        return _png()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    FakeWorker.submitted, FakeWorker.done, FakeWorker.refuse = [], None, None
    monkeypatch.setattr(api_moss, "MossWorker", FakeWorker)
    monkeypatch.delenv("OPENFIELD_MOSS_URL", raising=False)
    return TestClient(appmod.create_app(tmp_path))


BODY = {"prompt": "a ceramic mug, studio light", "headline": "SUMMER SALE", "width": 512, "height": 512, "seed": 4}


def test_a_poster_is_submitted_with_a_headline_box(client):
    r = client.post("/api/moss/posters", json=BODY)
    assert r.status_code == 202, r.text
    poster = r.json()["poster"]
    assert poster["state"] == "running"
    graph = FakeWorker.submitted[0]
    assert graph["clip"]["inputs"]["type"] == "moss_image"
    assert "SUMMER SALE" in graph["pos"]["inputs"]["text"]


def test_a_running_poster_stays_running(client):
    pid = client.post("/api/moss/posters", json=BODY).json()["poster"]["id"]
    assert client.get(f"/api/moss/posters/{pid}").json()["poster"]["state"] == "running"


def test_a_finished_poster_saves_the_image_and_a_receipt(client):
    pid = client.post("/api/moss/posters", json=BODY).json()["poster"]["id"]
    FakeWorker.done = {"ok": True, "outputs": [{"filename": "x.png", "subfolder": "", "type": "output"}], "messages": []}
    poster = client.get(f"/api/moss/posters/{pid}").json()["poster"]
    assert poster["state"] == "completed"
    assert poster["output"]["sha256"]
    img = client.get(f"/api/moss/posters/{pid}/output")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    rec = client.get(f"/api/moss/posters/{pid}/receipt").json()
    assert rec["output_sha256"] == poster["output"]["sha256"]
    assert rec["model_files"]["diffusion_model"].startswith("moss_image_sft")
    assert rec["params"]["headline"] == "SUMMER SALE" and rec["params"]["seed"] == 4
    assert rec["graph_sha256"] == poster["graph_sha256"]


def test_a_poster_the_server_failed_is_marked_failed_with_the_reason(client):
    pid = client.post("/api/moss/posters", json=BODY).json()["poster"]["id"]
    FakeWorker.done = {"ok": False, "outputs": [], "messages": [["execution_error", {"exception_message": "out of memory"}]]}
    poster = client.get(f"/api/moss/posters/{pid}").json()["poster"]
    assert poster["state"] == "failed"
    assert "out of memory" in poster["message"]
    assert client.get(f"/api/moss/posters/{pid}/output").status_code == 404


def test_a_poster_is_refused_when_the_server_is_not_running(client):
    FakeWorker.refuse = "The MOSS server did not answer. Is it running?"
    r = client.post("/api/moss/posters", json=BODY)
    assert r.status_code == 503 and "Is it running" in r.json()["detail"]


def test_a_remote_server_address_is_refused(client, monkeypatch):
    from openfield.forge import moss_worker
    monkeypatch.setattr(api_moss, "MossWorker", moss_worker.MossWorker)
    monkeypatch.setenv("OPENFIELD_MOSS_URL", "http://10.0.0.5:8189")
    r = client.post("/api/moss/posters", json=BODY)
    assert r.status_code == 503 and "loopback" in r.json()["detail"]


@pytest.mark.parametrize("change, status", [
    ({"width": 500}, 422),
    ({"prompt": "   "}, 422),
    ({"seed": -3}, 422),
    ({"boxes": [{"type": "obj", "x": 0.9, "y": 0, "w": 0.5, "h": 0.5}]}, 422),
])
def test_bad_requests_are_refused_before_anything_is_sent(client, change, status):
    r = client.post("/api/moss/posters", json={**BODY, **change})
    assert r.status_code == status
    assert FakeWorker.submitted == []


def test_unknown_posters_are_404(client):
    assert client.get("/api/moss/posters/" + "f" * 32).status_code == 404
    assert client.get("/api/moss/posters/not-an-id").status_code == 404


def test_the_list_shows_newest_first(client):
    client.post("/api/moss/posters", json={**BODY, "headline": "FIRST"})
    client.post("/api/moss/posters", json={**BODY, "headline": "SECOND"})
    names = [p["headline"] for p in client.get("/api/moss/posters").json()["posters"]]
    assert names == ["SECOND", "FIRST"]


def test_the_poster_types_are_listed_with_their_sizes(client):
    data = client.get("/api/moss/aspects").json()
    ids = [a["id"] for a in data["aspects"]]
    assert ids == ["square", "portrait", "story", "banner", "poster"]
    assert data["aspects"][2]["sizes"]["1024"] == [1024, 1824]


def test_a_poster_type_sets_the_size_the_graph_uses(client):
    r = client.post("/api/moss/posters", json={"prompt": "a mug", "aspect": "story", "base": 512})
    assert r.status_code == 202, r.text
    graph = FakeWorker.submitted[0]
    assert (graph["latent"]["inputs"]["width"], graph["latent"]["inputs"]["height"]) == (512, 912)
    assert r.json()["poster"]["params"]["width"] == 512 and r.json()["poster"]["params"]["height"] == 912


def test_an_unknown_poster_type_is_refused(client):
    r = client.post("/api/moss/posters", json={"prompt": "a mug", "aspect": "billboard"})
    assert r.status_code == 422 and FakeWorker.submitted == []
    r2 = client.post("/api/moss/layout", json={"aspect": "billboard", "boxes": []})
    assert r2.status_code == 422


def test_box_colours_reach_the_layout_and_bad_ones_are_refused(client):
    box = {"type": "obj", "x": 0.2, "y": 0.1, "w": 0.6, "h": 0.6, "desc": "bottle", "palette": ["c8102e", "#F5F1E6"]}
    r = client.post("/api/moss/layout", json={"boxes": [box], "aspect": "portrait"})
    assert r.status_code == 200, r.text
    assert r.json()["layout"]["items"][0]["palette"] == ["#C8102E", "#F5F1E6"]
    bad = client.post("/api/moss/layout", json={"boxes": [{**box, "palette": ["red"]}]})
    assert bad.status_code == 422 and "hex" in bad.json()["detail"]
