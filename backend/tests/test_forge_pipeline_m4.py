"""Milestone 4 (first slice): sequential pipelines. Each stage's image is re-uploaded and bound to the next stage.

Completion is driven through the job engine's own state change (_transition), so the hook wiring is tested too.
ComfyUI is never contacted: uploads, input checks and job submission are replaced.
"""
import copy
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openfield import api_forge, app as appmod
from openfield.forge import module, uploads
from openfield.forge import object_info as oi

PROMPT_A = {  # stage 1: a text prompt, saves an image
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "old text"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "a"}},
}
PROMPT_B = {  # stage 2: takes an image, saves an image
    "5": {"class_type": "LoadImage", "inputs": {"image": "old photo.jpg"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "b"}},
}
WF = {"id": "w" * 32, "name": "wf", "sha256": "0" * 64}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(oi, "load", (lambda real: (lambda home, client_get=None: real(home, None)))(oi.load))
    from openfield.api_forge import _INFO
    monkeypatch.setitem(_INFO, "info", None)
    c = TestClient(appmod.create_app(tmp_path))
    c.jobs_submitted = []

    def fake_submit(*args, **kw):
        prov = args[5]
        job = {"id": uuid.uuid4().hex, "kind": "app", "state": "QUEUED", "provider": "local", "provenance": prov,
               "output_file": None, "history": [], "workflow_id": args[0], "models": args[3], "notes": []}
        c.jobs_submitted.append(job)
        return job
    monkeypatch.setattr(c.app.state.jobs, "submit_app", fake_submit)
    monkeypatch.setattr(api_forge, "_comfy_has_input", lambda url, name: True)
    monkeypatch.setattr(uploads, "send_to_comfy", lambda url, data, info: uploads.comfy_name(info))
    return c


def _module(c, name, prompt, inputs, outputs):
    cn = module.new_contract(uuid.uuid4().hex, 1, name, "", WF, copy.deepcopy(prompt), inputs, outputs)
    module.ModuleStore(c.app.state.services.home / "forge" / "modules").save(cn)
    return cn.module_id


def _two_modules(c):
    a = _module(c, "Write", PROMPT_A,
                [module.Port(name="text", type="text", node="1", field="text", required=True)],
                [module.OutputPort(name="image", node="9")])
    b = _module(c, "Restyle", PROMPT_B,
                [module.Port(name="photo", type="image", node="5", field="image", required=True)],
                [module.OutputPort(name="image", node="9")])
    return a, b


def _pipeline(c, a, b, stage2_values=None):
    values2 = stage2_values if stage2_values is not None else {"photo": {"from_stage": "first", "output": "image"}}
    body = {"name": "Write then restyle", "stages": [
        {"id": "first", "module_id": a, "version": 1, "values": {"text": "a lighthouse"}},
        {"id": "second", "module_id": b, "version": 1, "values": values2},
    ]}
    r = c.post("/api/forge/pipelines", json=body)
    assert r.status_code == 201, r.text
    return r.json()["pipeline"]["id"]


def _finish(c, job, state):
    """Move a job to a terminal state through the engine's own transition, writing the output first if it completed."""
    if state == "COMPLETED":
        d = c.app.state.services.home / "forge" / "runs" / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "output.png").write_bytes(_png())
        job["output_file"] = {"name": "output.png", "sha256": "x", "bytes": 1, "media": "image"}
    c.app.state.jobs._transition(job, state)


# ---- validation ------------------------------------------------------------------------

def _validate(c, stages):
    from openfield.forge import pipeline
    return pipeline.validate([pipeline.StageSpec.model_validate(s) for s in stages], module.ModuleStore(c.app.state.services.home / "forge" / "modules"))


def test_a_valid_pipeline_has_no_problems(client):
    a, b = _two_modules(client)
    stages = [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
              {"id": "second", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "first", "output": "image"}}}]
    assert _validate(client, stages) == []


@pytest.mark.parametrize("name, stage2, expected", [
    ("later stage", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                  {"id": "second", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "second", "output": "image"}}}],
     "must take the output of an earlier stage"),
    ("missing stage", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                    {"id": "second", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "ghost", "output": "image"}}}],
     "does not exist"),
    ("missing output", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                     {"id": "second", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "first", "output": "video"}}}],
     "does not have"),
    ("text port fed from a stage", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                                 {"id": "second", "module_id": a, "version": 1, "values": {"text": {"from_stage": "first", "output": "image"}}}],
     "not an image"),
    ("required image missing", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                             {"id": "second", "module_id": b, "version": 1, "values": {}}],
     "is required"),
    ("duplicate stage id", lambda a, b: [{"id": "first", "module_id": a, "version": 1, "values": {"text": "x"}},
                                         {"id": "first", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "first", "output": "image"}}}],
     "used twice"),
    ("unknown module version", lambda a, b: [{"id": "first", "module_id": a, "version": 9, "values": {"text": "x"}}],
     "no version 9"),
])
def test_bad_pipelines_are_refused_with_a_reason(client, name, stage2, expected):
    a, b = _two_modules(client)
    problems = _validate(client, stage2(a, b))
    assert any(expected in p for p in problems), problems


def test_create_route_refuses_an_invalid_pipeline(client):
    a, b = _two_modules(client)
    body = {"name": "bad", "stages": [{"id": "second", "module_id": b, "version": 1, "values": {"photo": {"from_stage": "first", "output": "image"}}}]}
    r = client.post("/api/forge/pipelines", json=body)
    assert r.status_code == 422, r.text
    assert "does not exist" in r.json()["detail"]


# ---- runs ------------------------------------------------------------------------------

def test_two_stage_run_hands_the_image_to_the_next_stage(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    r = client.post(f"/api/forge/pipelines/{pid}/run")
    assert r.status_code == 202, r.text
    run = r.json()["run"]
    assert run["state"] == "running"
    assert len(client.jobs_submitted) == 1
    first = client.jobs_submitted[0]
    assert first["provenance"]["pipeline_run_id"] == run["id"] and first["provenance"]["stage_index"] == 0

    _finish(client, first, "COMPLETED")

    assert len(client.jobs_submitted) == 2, "the second stage must start after the first completes"
    second = client.jobs_submitted[1]
    assert second["provenance"]["stage_index"] == 1
    handed = second["provenance"]["values"]["photo"]
    assert handed.startswith("openfield_") and handed.endswith(".png")
    assert second["provenance"]["values"] == {"photo": handed}

    _finish(client, second, "COMPLETED")
    final = client.get(f"/api/forge/pipeline-runs/{run['id']}").json()["run"]
    assert final["state"] == "completed"
    assert final["result"]["stage_id"] == "second" and final["result"]["file"] == "output.png"
    assert [s["state"] for s in final["stages"]] == ["completed", "completed"]


def test_a_failed_stage_stops_the_run(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    run = client.post(f"/api/forge/pipelines/{pid}/run").json()["run"]
    _finish(client, client.jobs_submitted[0], "FAILED")
    assert len(client.jobs_submitted) == 1, "the second stage must never be submitted"
    final = client.get(f"/api/forge/pipeline-runs/{run['id']}").json()["run"]
    assert final["state"] == "failed"
    assert final["stages"][0]["state"] == "failed" and final["stages"][1]["state"] == "pending"
    assert "did not complete" in final["message"]


def test_a_cancelled_stage_stops_the_run(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    run = client.post(f"/api/forge/pipelines/{pid}/run").json()["run"]
    _finish(client, client.jobs_submitted[0], "CANCELLED")
    assert len(client.jobs_submitted) == 1
    assert client.get(f"/api/forge/pipeline-runs/{run['id']}").json()["run"]["state"] == "failed"


def test_a_failed_upload_between_stages_fails_the_run_with_its_reason(client, monkeypatch):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    run = client.post(f"/api/forge/pipelines/{pid}/run").json()["run"]

    def refuse(url, data, info):
        raise uploads.UploadError("ComfyUI did not accept the image (ConnectError). Is ComfyUI running?")
    monkeypatch.setattr(uploads, "send_to_comfy", refuse)
    _finish(client, client.jobs_submitted[0], "COMPLETED")
    final = client.get(f"/api/forge/pipeline-runs/{run['id']}").json()["run"]
    assert final["state"] == "failed"
    assert "Is ComfyUI running" in final["message"]
    assert len(client.jobs_submitted) == 1


def test_a_run_keeps_the_module_version_it_started_with(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    # A new version of the first module must not change the pipeline.
    newer = module.new_contract(a, 2, "Write", "", WF, copy.deepcopy(PROMPT_A), [], [module.OutputPort(name="image", node="9")])
    module.ModuleStore(client.app.state.services.home / "forge" / "modules").save(newer)
    client.post(f"/api/forge/pipelines/{pid}/run")
    assert client.jobs_submitted[0]["provenance"]["module_version"] == 1
    assert client.get(f"/api/forge/pipelines/{pid}").json()["pipeline"]["stages"][0]["version"] == 1


def test_pipeline_validate_route_and_list(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    assert client.post(f"/api/forge/pipelines/{pid}/validate").json() == {"valid": True, "problems": []}
    assert [p["id"] for p in client.get("/api/forge/pipelines").json()["pipelines"]] == [pid]
    assert client.get("/api/forge/pipelines/" + "e" * 32).status_code == 404


def test_saving_again_bumps_the_revision(client):
    a, b = _two_modules(client)
    pid = _pipeline(client, a, b)
    from openfield.forge import pipeline
    spec = pipeline.PipelineSpec(name="Renamed", stages=[pipeline.StageSpec(id="first", module_id=a, version=1, values={"text": "y"})])
    rec = pipeline.PipelineStore(client.app.state.services.home / "forge" / "pipelines").save(spec, pid)
    assert rec["revision"] == 2 and rec["name"] == "Renamed"
