"""Milestone 2 tests: registry, discovery, status, downloads, workflows, wizard, safety.

Most tests use synthetic model folders and MOCKED HTTP (httpx.MockTransport).
A mocked download proves only our code's behaviour, not that any real source works.
"""
import hashlib
import json
import os
import struct
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from openfield.app import create_app
from openfield.comfy_detect import is_comfy_root
from openfield.config import Settings, SettingsStore
from openfield.models import downloads as dl
from openfield.models.catalog import load_catalog
from openfield.models.discovery import Root, read_header, same_size_groups, scan_roots, sha256_cached
from openfield.models.schema import CatalogModel
from openfield.models.status import model_status
from openfield.models.store import ModelRegistryStore, RegistryData
from openfield.wizard import WizardStore, mark_done, STEPS, WizardState, WizardError, recommend_starter
from openfield.workflows.registry import Workflow, evaluate, load_workflows, CoreNodes

CORE_NODES = '''
NODE_CLASS_MAPPINGS = {
    "UNETLoader": 1, "CLIPLoader": 1, "CLIPTextEncode": 1, "VAELoader": 1, "KSampler": 1,
    "VAEDecode": 1, "SaveImage": 1, "CLIPVisionLoader": 1, "CLIPVisionEncode": 1, "EmptySD3LatentImage": 1,
}
class WanImageToVideo:
    pass
'''


def safetensors_file(path: Path, size: int = 4096) -> Path:
    header = b'{"__metadata__":{"format":"pt"}}'
    body = struct.pack("<Q", len(header)) + header
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + b"\0" * max(0, size - len(body)))
    return path


@pytest.fixture
def comfy(tmp_path):
    root = tmp_path / "ComfyUI_portable"
    (root / "ComfyUI" / "comfy_extras").mkdir(parents=True)
    (root / "ComfyUI" / "nodes.py").write_text(CORE_NODES, encoding="utf-8")
    (root / "ComfyUI" / "main.py").write_text("# main", encoding="utf-8")
    (root / "ComfyUI" / "custom_nodes" / "ComfyUI-GGUF").mkdir(parents=True)
    for d in ("diffusion_models", "text_encoders", "vae", "clip_vision", "unet", "clip"):
        (root / "ComfyUI" / "models" / d).mkdir(parents=True)
    return root


@pytest.fixture
def app_and_home(tmp_path, comfy):
    home = tmp_path / "home"
    app = create_app(home)
    SettingsStore(home).save(Settings(comfy_root=str(comfy), projects_dir=str(home / "projects")))
    return app, home


@pytest.fixture
def client(app_and_home):
    app, _ = app_and_home
    return TestClient(app)


def snapshot_tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_size, p.stat().st_mtime_ns)
    return out


# ---------------------------------------------------------------- registry & catalogue

def test_catalog_loads_four_candidates_with_evidence():
    cat = load_catalog()
    assert cat.errors == []
    assert set(cat.models) == {"z-image-turbo", "flux-2-klein-4b", "qwen-image-2.1", "wan-2.1-i2v-14b-480p",
                               "ltx-2.5-i2v", "minimax-h3"}  # the last two are optional installs
    assert cat.models["qwen-image-2.1"].license.status == "restricted"
    assert cat.models["z-image-turbo"].license.status == "verified"


def test_malformed_catalog_entry_is_skipped_not_fatal(tmp_path):
    good = json.loads((Path(__file__).resolve().parents[1] / "openfield" / "data" / "model_catalog.json").read_text())
    bad = {"id": "Bad ID With Spaces", "name": "x"}
    good["models"].append(bad)
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(good))
    cat = load_catalog(p)
    assert len(cat.models) == 6
    assert any("Bad ID" in e for e in cat.errors)


def test_duplicate_catalog_id_reported(tmp_path):
    good = json.loads((Path(__file__).resolve().parents[1] / "openfield" / "data" / "model_catalog.json").read_text())
    good["models"].append(dict(good["models"][0]))
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(good))
    cat = load_catalog(p)
    assert any("duplicate catalog id" in e for e in cat.errors)


def test_download_filename_traversal_rejected():
    from openfield.models.schema import DownloadFile
    with pytest.raises(Exception):
        DownloadFile(relpath="../../evil.safetensors", url="https://huggingface.co/x", size_bytes=10, component_role="vae")
    with pytest.raises(Exception):
        DownloadFile(relpath="x.safetensors", url="http://evil.example/x", size_bytes=10, component_role="vae")


def test_malformed_registry_is_moved_aside_and_recovered(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "registry.json").write_text("{this is not json")
    store = ModelRegistryStore(models)
    data = store.load()
    assert data.identities == {}
    assert store.load_errors and (models / "registry.json.corrupt").exists()


def test_registry_section_malformed_is_ignored_with_report(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "registry.json").write_text(json.dumps({"schema_version": 1, "identities": "oops", "validations": {}}))
    store = ModelRegistryStore(models)
    data = store.load()
    assert data.identities == {} and any("identities" in e for e in store.load_errors)


# ---------------------------------------------------------------- discovery & identity

def test_unknown_files_are_not_identified_as_models(comfy):
    safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "some_random_model.safetensors")
    cat = load_catalog()
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    reg = RegistryData()
    z = model_status(cat.models["z-image-turbo"], scan, reg)
    assert z["discovered"] is False and z["category"] == "missing"


def test_similar_filename_does_not_match(comfy):
    # near-miss name: must not match the exact filename
    safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16_v2.safetensors")
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    z = model_status(load_catalog().models["z-image-turbo"], scan, RegistryData())
    assert z["variants"][0]["discovered"] is False


def test_discovered_file_is_not_identified_until_user_confirms(comfy):
    f = safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    z = model_status(load_catalog().models["z-image-turbo"], scan, RegistryData())
    assert z["discovered"] is True and z["identified"] is False and z["validated"] is False
    assert z["variants"][0]["components"][0]["state"] == "found"
    assert z["open_weight_eligibility"] == "review"
    assert f.exists()


def test_same_size_is_reported_not_treated_as_identity(comfy):
    d = comfy / "ComfyUI" / "models" / "diffusion_models"
    safetensors_file(d / "a.safetensors", 4096)
    safetensors_file(d / "b.safetensors", 4096)
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    groups = same_size_groups(scan.files)
    assert len(groups) == 1 and len(groups[0]) == 2  # grouped only; never declared identical


def test_missing_directory_is_reported_without_crash(tmp_path):
    scan = scan_roots([Root(tmp_path / "does-not-exist", "user_shared", "gone")])
    assert scan.files == [] and scan.problems[0].kind == "missing_dir"


def test_broken_symlink_is_reported(comfy):
    target = comfy / "ComfyUI" / "models" / "vae" / "missing_target.safetensors"
    link = comfy / "ComfyUI" / "models" / "vae" / "ae.safetensors"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not permitted on this system (Windows needs Developer Mode or admin)")
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    assert any(p.kind == "broken_link" for p in scan.problems)


def test_permission_error_is_reported_not_fatal(comfy, monkeypatch):
    f = safetensors_file(comfy / "ComfyUI" / "models" / "vae" / "ae.safetensors")
    real_stat = Path.stat

    def fake_stat(self, *a, **kw):
        if self == f:
            raise PermissionError("denied")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", fake_stat)
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    assert any(p.kind == "permission" for p in scan.problems)


def test_malformed_safetensors_header_flagged(tmp_path):
    p = tmp_path / "bad.safetensors"
    p.write_bytes(struct.pack("<Q", 20) + b"not-json-at-all!!!!!" + b"\0" * 10)
    fmt, ok, err = read_header(str(p))
    assert ok is False and err


def test_sha256_cache_invalidates_when_file_changes(tmp_path):
    p = safetensors_file(tmp_path / "w.safetensors", 4096)
    scan = scan_roots([Root(tmp_path, "external", "t")])
    entry = scan.files[0]
    cache: dict = {}
    d1, cached1 = sha256_cached(entry, cache)
    d2, cached2 = sha256_cached(entry, cache)
    assert cached1 is False and cached2 is True and d1 == d2 == hashlib.sha256(p.read_bytes()).hexdigest()
    p.write_bytes(p.read_bytes() + b"\0")
    entry2 = scan_roots([Root(tmp_path, "external", "t")]).files[0]
    _, cached3 = sha256_cached(entry2, cache)
    assert cached3 is False


def test_identity_goes_stale_when_file_changes(comfy):
    f = safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")
    st = f.stat()
    reg = RegistryData(identities={"z-image-turbo": {"bf16-publisher": {"diffusion": {
        "path": str(f), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "confirmed_at": "t"}}}})
    scan = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    comp = model_status(load_catalog().models["z-image-turbo"], scan, reg)["variants"][0]["components"][0]
    assert comp["state"] == "identified"
    time.sleep(0.01)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    scan2 = scan_roots([Root(comfy / "ComfyUI" / "models", "external", "comfy")])
    comp2 = model_status(load_catalog().models["z-image-turbo"], scan2, reg)["variants"][0]["components"][0]
    assert comp2["state"] == "found" and comp2["identity_stale"] is True


# ---------------------------------------------------------------- licences & eligibility

def test_restricted_license_is_not_eligible(comfy):
    scan = scan_roots([])
    q = model_status(load_catalog().models["qwen-image-2.1"], scan, RegistryData())
    assert q["open_weight_eligibility"] == "not_eligible"


def test_no_model_is_marked_eligible_yet():
    scan = scan_roots([])
    for m in load_catalog().models.values():
        assert model_status(m, scan, RegistryData())["open_weight_eligibility"] != "eligible"


def test_unverified_auxiliary_license_blocks_eligible():
    cat = load_catalog()
    m = cat.models["z-image-turbo"]
    assert any(c.license_status != "verified" for v in m.variants for c in v.components)


# ---------------------------------------------------------------- downloads (MOCKED transport)

def _model_with_download(content: bytes, sha: str | None = None, url="https://huggingface.co/x/y/resolve/main/f.safetensors"):
    m = load_catalog().models["z-image-turbo"].model_dump()
    v = m["variants"][0]
    v["download"] = {"source_repo": "https://huggingface.co/x/y", "revision": None,
                     "checksum_authority": "publisher" if sha else "none",
                     "files": [{"relpath": "z_image_turbo_bf16.safetensors", "url": url, "size_bytes": len(content),
                                "sha256": sha, "component_role": "diffusion"}]}
    return CatalogModel.model_validate(m), v["id"]


def _manager(tmp_path, handler, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return dl.DownloadManager(tmp_path / "managed",
                              client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
                              sleep=lambda s: sleeps.append(s))


def _plan(model, variant_id, managed, free=None):
    v = next(x for x in model.variants if x.id == variant_id)
    plan = dl.make_plan(model, v, managed, ["img-z-image-turbo"])
    if free is not None:
        plan.free_bytes = free
    return plan


def test_plan_makes_no_network_request(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used while planning")
    monkeypatch.setattr(httpx.Client, "stream", boom)
    monkeypatch.setattr(httpx.Client, "get", boom)
    model, vid = _model_with_download(b"x" * 100)
    plan = _plan(model, vid, tmp_path / "managed")
    assert plan.total_bytes == 100 and plan.files[0]["checksum"] == "none"


def test_no_verified_source_means_no_plan(tmp_path):
    model = load_catalog().models["z-image-turbo"]
    with pytest.raises(dl.NoVerifiedSource):
        dl.make_plan(model, model.variants[0], tmp_path / "managed", [])


def test_start_refused_without_explicit_confirmation(tmp_path):
    content = b"A" * 64
    model, vid = _model_with_download(content)
    m = _manager(tmp_path, lambda r: httpx.Response(200, content=content))
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    for bad in (False, None, "yes", 1):
        with pytest.raises(dl.DownloadError):
            m.start(plan.plan_id, confirm=bad)
    assert not (tmp_path / "managed").exists() or not any((tmp_path / "managed").rglob("*.part"))


def test_download_success_verifies_size_and_checksum(tmp_path):
    content = b"B" * 5000
    sha = hashlib.sha256(content).hexdigest()
    model, vid = _model_with_download(content, sha)
    m = _manager(tmp_path, lambda r: httpx.Response(200, content=content))
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.start(plan.plan_id, confirm=True)
    m.wait(job.job_id)
    assert job.state == "completed", job.error
    out = Path(plan.destination) / "z_image_turbo_bf16.safetensors"
    assert out.read_bytes() == content
    assert job.results[0]["checksum"] == "publisher-verified"


def test_checksum_mismatch_removes_file(tmp_path):
    content = b"C" * 100
    model, vid = _model_with_download(content, "0" * 64)
    m = _manager(tmp_path, lambda r: httpx.Response(200, content=content))
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.wait(m.start(plan.plan_id, confirm=True).job_id)
    assert job.state == "failed" and "sha256 mismatch" in job.error
    assert not (Path(plan.destination) / "z_image_turbo_bf16.safetensors").exists()


def test_transient_5xx_is_retried_then_succeeds(tmp_path):
    content = b"D" * 300
    model, vid = _model_with_download(content)
    calls = {"n": 0}
    sleeps: list = []

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, content=content)

    m = _manager(tmp_path, handler, sleeps)
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.wait(m.start(plan.plan_id, confirm=True).job_id)
    assert job.state == "completed" and calls["n"] == 2 and sleeps == [1.0]


def test_retries_are_bounded(tmp_path):
    content = b"E" * 300
    model, vid = _model_with_download(content)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500)

    m = _manager(tmp_path, handler)
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.wait(m.start(plan.plan_id, confirm=True).job_id)
    assert job.state == "failed" and calls["n"] == dl.MAX_ATTEMPTS and "failed after" in job.error


def test_client_error_is_not_retried(tmp_path):
    content = b"F" * 300
    model, vid = _model_with_download(content)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404)

    m = _manager(tmp_path, handler)
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.wait(m.start(plan.plan_id, confirm=True).job_id)
    assert job.state == "failed" and calls["n"] == 1 and "HTTP 404" in job.error
    assert not any(Path(plan.destination).rglob("*.part"))


def test_cancel_mid_stream_cleans_partial(tmp_path):
    content = b"H" * (3 * dl.CHUNK)
    model, vid = _model_with_download(content)
    holder = {}

    def body():
        yield content[: dl.CHUNK]
        while "job" not in holder:  # job is created after start() returns
            time.sleep(0.01)
        holder["m"].cancel(holder["job"].job_id)
        yield content[dl.CHUNK: 2 * dl.CHUNK]
        yield content[2 * dl.CHUNK:]

    m = _manager(tmp_path, lambda r: httpx.Response(200, content=body()))
    holder["m"] = m
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    job = m.start(plan.plan_id, confirm=True)
    holder["job"] = job
    m.wait(job.job_id)
    assert job.state == "cancelled"
    assert not any(Path(plan.destination).rglob("*.part"))
    assert not (Path(plan.destination) / "z_image_turbo_bf16.safetensors").exists()


def test_resume_within_run_uses_range_header(tmp_path):
    content = b"I" * 1000
    model, vid = _model_with_download(content)
    seen = {}

    def handler(req):
        seen["range"] = req.headers.get("range")
        return httpx.Response(206, content=content[400:])

    m = _manager(tmp_path, handler)
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    dest = Path(plan.destination)
    dest.mkdir(parents=True)
    (dest / "z_image_turbo_bf16.safetensors.part").write_bytes(content[:400])
    job = m.wait(m.start(plan.plan_id, confirm=True).job_id)
    assert seen["range"] == "bytes=400-" and job.state == "completed"
    assert (dest / "z_image_turbo_bf16.safetensors").read_bytes() == content


def test_disk_space_rejection(tmp_path):
    content = b"J" * 100
    model, vid = _model_with_download(content)
    m = _manager(tmp_path, lambda r: httpx.Response(200, content=content))
    plan = m.remember(_plan(model, vid, tmp_path / "managed", free=10))
    with pytest.raises(dl.InsufficientDisk):
        m.start(plan.plan_id, confirm=True)


def test_existing_file_is_never_overwritten(tmp_path):
    content = b"K" * 100
    model, vid = _model_with_download(content)
    m = _manager(tmp_path, lambda r: httpx.Response(200, content=content))
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    dest = Path(plan.destination)
    dest.mkdir(parents=True)
    (dest / "z_image_turbo_bf16.safetensors").write_bytes(b"user data")
    with pytest.raises(dl.DownloadError, match="already exists"):
        m.start(plan.plan_id, confirm=True)
    assert (dest / "z_image_turbo_bf16.safetensors").read_bytes() == b"user data"


def test_plan_is_single_use_and_expires(tmp_path):
    content = b"L" * 100
    model, vid = _model_with_download(content)
    now = [dl.datetime.now(dl.timezone.utc)]
    m = dl.DownloadManager(tmp_path / "managed",
                           client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))),
                           sleep=lambda s: None, clock=lambda: now[0])
    plan = m.remember(_plan(model, vid, tmp_path / "managed"))
    m.wait(m.start(plan.plan_id, confirm=True).job_id)
    with pytest.raises(dl.DownloadError):
        m.start(plan.plan_id, confirm=True)  # already used
    plan2 = m.remember(_plan(model, vid, tmp_path / "managed"))
    now[0] = plan2.expires_at + dl.timedelta(seconds=1)
    with pytest.raises(dl.DownloadError, match="expired"):
        m.get_plan(plan2.plan_id)


# ---------------------------------------------------------------- workflows

def test_workflow_missing_pack_reported():
    cat = load_catalog()
    wf = load_workflows().items["i2v-wan-2.1-14b-480p"]
    scan = scan_roots([])
    st = {m.id: model_status(m, scan, RegistryData()) for m in cat.models.values()}
    nodes = CoreNodes(CORE_NODES + "\nclass CLIPVisionLoader(object): pass")
    ev = evaluate(wf, st, nodes, custom_packs_present=set(), validated_ids=set())
    assert ev["status"] == "missing_models"  # models absent is checked first
    assert "ComfyUI-GGUF" in wf.required_packs and ev["missing_packs"] == ["ComfyUI-GGUF"]


def test_workflow_missing_node_reported(comfy):
    wf = Workflow(id="t", version="1", name="t", task="image", models=[],
                  required_nodes=["NotARealNodeClass"], license_requirements="x")
    ev = evaluate(wf, {}, CoreNodes(CORE_NODES), set(), set())
    assert ev["status"] == "missing_nodes" and ev["missing_nodes"] == ["NotARealNodeClass"]


def test_unknown_engine_is_not_reported_as_ready():
    wf = Workflow(id="t", version="1", name="t", task="image", models=[], license_requirements="x")
    assert evaluate(wf, {}, None, set(), set())["status"] == "unknown_engine"


def test_requirements_met_is_not_validated():
    wf = Workflow(id="t", version="1", name="t", task="image", models=[], license_requirements="x")
    ev = evaluate(wf, {}, CoreNodes(CORE_NODES), set(), set())
    assert ev["status"] == "requirements_met_untested" and ev["smoke_tested"] is False


def test_license_blocked_takes_precedence():
    cat = load_catalog()
    wf = load_workflows().items["img-qwen-image-2.1"]
    st = {m.id: model_status(m, scan_roots([]), RegistryData()) for m in cat.models.values()}
    ev = evaluate(wf, st, CoreNodes(CORE_NODES), {"ComfyUI-GGUF"}, set())
    assert ev["status"] == "license_blocked" and ev["blockers"]


def test_workflow_declares_unsupported_controls():
    wf = load_workflows().items["img-z-image-turbo"]
    assert "camera_move" in wf.unsupported_controls and "camera_move" not in wf.supported_params


def test_real_catalog_workflows_load_clean():
    lw = load_workflows()
    assert lw.errors == [] and len(lw.items) == 7


def test_recommendation_never_picks_blocked_workflow():
    evals = [{"id": "q", "status": "license_blocked", "blockers": ["x"], "name": "Q", "missing_models": [],
              "missing_packs": [], "unconfirmed_files": [], "supported_params": []}]
    rec = recommend_starter(evals)
    assert rec["workflow_id"] is None and rec["blockers"] == ["x"]


# ---------------------------------------------------------------- wizard

def test_wizard_state_survives_restart(tmp_path):
    ws = WizardStore(tmp_path)
    st = WizardState()
    mark_done(st, "welcome", verified=True)
    ws.save(st)
    again = WizardStore(tmp_path).load()
    assert again.completed == ["welcome"]


def test_wizard_step_not_marked_without_verified_action(tmp_path):
    st = WizardState()
    with pytest.raises(WizardError):
        mark_done(st, "download", verified=False, reason="no download yet")
    assert "download" not in st.completed


def test_wizard_corrupt_file_recovered(tmp_path):
    (tmp_path / "wizard.json").write_text("{broken")
    st = WizardStore(tmp_path).load()
    assert st.completed == [] and (tmp_path / "wizard.json.corrupt").exists()


def test_wizard_api_verifies_and_resets(client):
    assert client.post("/api/wizard/step", json={"step": "download", "action": "complete"}).status_code == 409
    assert client.post("/api/wizard/step", json={"step": "welcome", "action": "complete"}).status_code == 200
    assert client.post("/api/wizard/step", json={"step": "welcome", "action": "skip"}).status_code == 409  # welcome is not skippable
    st = client.get("/api/wizard").json()
    assert "welcome" in st["completed"]
    assert client.post("/api/wizard/reset").json()["reset"] is True
    assert client.get("/api/wizard").json()["completed"] == []


def test_wizard_completion_blocked_while_steps_pending(client):
    r = client.post("/api/wizard/step", json={"step": "completion", "action": "complete"})
    assert r.status_code == 409


def test_wizard_completes_once_other_steps_are_done_or_skipped(client):
    assert client.post("/api/wizard/step", json={"step": "welcome", "action": "complete"}).status_code == 200
    assert client.post("/api/wizard/step", json={"step": "system_check", "action": "complete"}).status_code == 200
    client.post("/api/models/scan")
    assert client.post("/api/wizard/step", json={"step": "model_discovery", "action": "complete"}).status_code == 200
    assert client.post("/api/wizard/step", json={"step": "workflow_readiness", "action": "complete"}).status_code == 200
    assert client.post("/api/wizard/step", json={"step": "starter", "action": "complete"}).status_code == 200
    assert client.post("/api/wizard/step", json={"step": "download", "action": "skip"}).status_code == 200
    r = client.post("/api/wizard/step", json={"step": "completion", "action": "complete"})
    assert r.status_code == 200 and "completion" in r.json()["completed"]


def test_wizard_model_discovery_needs_a_real_scan(client):
    assert client.post("/api/wizard/step", json={"step": "model_discovery", "action": "complete"}).status_code == 409
    client.post("/api/models/scan")
    assert client.post("/api/wizard/step", json={"step": "model_discovery", "action": "complete"}).status_code == 200


# ---------------------------------------------------------------- safety: API & filesystem

def test_external_model_cannot_be_removed_via_api(app_and_home, comfy):
    app, home = app_and_home
    client = TestClient(app)
    f = safetensors_file(comfy / "ComfyUI" / "models" / "vae" / "ae.safetensors")
    r = client.post("/api/models/z-image-turbo/managed/remove",
                    json={"variant": "bf16-publisher", "confirm_model_id": "z-image-turbo"})
    assert r.status_code == 404 and "nothing was removed" in r.json()["detail"]
    assert f.exists()


def test_managed_removal_requires_typed_model_id(app_and_home):
    app, home = app_and_home
    client = TestClient(app)
    r = client.post("/api/models/z-image-turbo/managed/remove",
                    json={"variant": "bf16-publisher", "confirm_model_id": "yes"})
    assert r.status_code == 400


def test_identify_rejects_path_outside_scan(client, comfy, tmp_path):
    outside = safetensors_file(tmp_path / "elsewhere" / "z_image_turbo_bf16.safetensors")
    r = client.post("/api/models/z-image-turbo/identify",
                    json={"variant": "bf16-publisher", "role": "diffusion", "path": str(outside), "confirm": True})
    assert r.status_code == 400


def test_identify_requires_confirm_and_then_records(client, comfy):
    f = safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")
    client.post("/api/models/scan")
    body = {"variant": "bf16-publisher", "role": "diffusion", "path": str(f)}
    assert client.post("/api/models/z-image-turbo/identify", json={**body, "confirm": False}).status_code == 400
    assert client.post("/api/models/z-image-turbo/identify", json={**body, "confirm": True}).status_code == 200
    detail = client.get("/api/models/z-image-turbo").json()
    assert detail["variants"][0]["components"][0]["state"] == "identified"
    assert detail["identified"] is False  # other components still unconfirmed


def test_verify_hash_only_for_scanned_candidates(client, comfy):
    f = safetensors_file(comfy / "ComfyUI" / "models" / "vae" / "ae.safetensors")
    client.post("/api/models/scan")
    ok = client.post("/api/models/z-image-turbo/verify-hash",
                     json={"variant": "bf16-publisher", "role": "vae", "path": str(f)})
    assert ok.status_code == 200 and "No publisher checksum" in ok.json()["authority"]
    bad = client.post("/api/models/z-image-turbo/verify-hash",
                      json={"variant": "bf16-publisher", "role": "vae", "path": str(comfy / "x.safetensors")})
    assert bad.status_code == 400


def test_model_roots_add_and_remove_never_touch_files(client, tmp_path, app_and_home):
    _, home = app_and_home
    shared = tmp_path / "shared_models"
    shared.mkdir()
    keep = safetensors_file(shared / "keep.safetensors")
    before = snapshot_tree(shared)
    assert client.post("/api/model-roots", json={"path": str(shared)}).status_code == 200
    assert client.post("/api/model-roots", json={"path": str(tmp_path / "nope")}).status_code == 400
    assert client.post("/api/model-roots/remove", json={"path": str(shared)}).json()["files_touched"] == 0
    assert snapshot_tree(shared) == before and keep.exists()


def test_comfy_root_must_be_a_real_comfyui(client, tmp_path):
    assert client.post("/api/comfy/root", json={"path": str(tmp_path)}).status_code == 400


def test_no_network_or_download_during_startup(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used during startup")
    monkeypatch.setattr(httpx, "Client", boom)
    app = create_app(tmp_path / "home")
    assert app.state.services.downloads.job  # constructed, not started
    assert app.state.services.completed_downloads == []


def test_no_network_before_download_confirmation(app_and_home, comfy):
    app, home = app_and_home

    def boom():
        raise AssertionError("network used before confirmation")
    app.state.services.downloads._client_factory = boom
    client = TestClient(app)
    model_id = "z-image-turbo"
    # no verified source: plan is refused without any request
    r = client.post(f"/api/models/{model_id}/plan", json={"variant": "bf16-publisher"})
    assert r.status_code == 409
    r2 = client.post("/api/downloads/not-a-plan/start", json={"confirm": False})
    assert r2.status_code == 400


def test_external_comfyui_config_and_files_unchanged(app_and_home, comfy):
    app, home = app_and_home
    client = TestClient(app)
    safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")
    before = snapshot_tree(comfy)
    client.get("/api/models")
    client.get("/api/workflows")
    client.get("/api/comfy/detect")
    client.post("/api/models/scan")
    client.post("/api/models/z-image-turbo/verify-hash",
                json={"variant": "bf16-publisher", "role": "diffusion",
                      "path": str(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")})
    assert snapshot_tree(comfy) == before


def test_api_model_list_has_separate_status_fields(client, comfy):
    safetensors_file(comfy / "ComfyUI" / "models" / "diffusion_models" / "z_image_turbo_bf16.safetensors")
    body = client.get("/api/models").json()
    z = next(m for m in body["models"] if m["id"] == "z-image-turbo")
    for key in ("discovered", "identified", "validated", "smoke_tested", "license", "open_weight_eligibility"):
        assert key in z
    assert z["smoke_tested"] is False and z["validated"] is False and z["identified"] is False


def test_api_workflows_list_uses_real_catalog(client):
    body = client.get("/api/workflows").json()
    ids = {w["id"] for w in body["workflows"]}
    assert "img-z-image-turbo" in ids
    assert all(w["smoke_tested"] is False for w in body["workflows"])


def test_api_download_records_managed_file_then_removal_deletes_only_it(app_and_home, monkeypatch):
    """MOCKED transport. Proves the API path: plan -> confirm -> download -> managed record -> typed removal."""
    import hashlib as _h
    app, home = app_and_home
    content = b"M" * 2048
    model, vid = _model_with_download(content, _h.sha256(content).hexdigest())
    services = app.state.services
    services.catalog.models[model.id] = model
    services.downloads._client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content)))
    client = TestClient(app)

    plan = client.post(f"/api/models/{model.id}/plan", json={"variant": vid})
    assert plan.status_code == 200, plan.text
    assert plan.json()["network"].startswith("No request has been made yet")

    started = client.post(f"/api/downloads/{plan.json()['plan_id']}/start", json={"confirm": True})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    services.downloads.wait(job_id)
    assert client.get(f"/api/downloads/{job_id}").json()["state"] == "completed"

    managed_file = services.managed_dir / model.id / vid / "z_image_turbo_bf16.safetensors"
    assert managed_file.read_bytes() == content
    reg = services.registry.load()
    rec = reg.managed[model.id][vid]["diffusion"]
    assert rec["checksum"] == "publisher-verified" and rec["path"] == str(managed_file)

    # an external file must survive the managed removal
    external = safetensors_file(services.home.parent / "external_keep.safetensors")
    refused = client.post(f"/api/models/{model.id}/managed/remove",
                          json={"variant": vid, "confirm_model_id": "wrong"})
    assert refused.status_code == 400 and managed_file.exists()
    ok = client.post(f"/api/models/{model.id}/managed/remove", json={"variant": vid, "confirm_model_id": model.id})
    assert ok.status_code == 200 and not managed_file.exists() and external.exists()


def test_download_refused_when_plan_used_twice_via_api(app_and_home):
    app, home = app_and_home
    content = b"N" * 64
    model, vid = _model_with_download(content)
    services = app.state.services
    services.catalog.models[model.id] = model
    services.downloads._client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content)))
    client = TestClient(app)
    pid = client.post(f"/api/models/{model.id}/plan", json={"variant": vid}).json()["plan_id"]
    first = client.post(f"/api/downloads/{pid}/start", json={"confirm": True})
    assert first.status_code == 202
    services.downloads.wait(first.json()["job_id"])
    second = client.post(f"/api/downloads/{pid}/start", json={"confirm": True})
    assert second.status_code == 400
