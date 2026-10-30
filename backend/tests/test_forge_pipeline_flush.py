"""A finished pipeline stage frees ComfyUI's memory before the next stage starts, and a failed free stops the run."""
from openfield.forge import pipeline


def _run(tmp_path, stages=2):
    runs = pipeline.RunStore(tmp_path / "runs")
    run = {"id": "a" * 32, "pipeline_id": "b" * 32, "pipeline_name": "p", "pipeline_revision": 1, "started_at": "t",
           "state": "running", "message": None, "result": None,
           "spec": [{"id": f"s{i}", "module_id": "m", "version": 1, "values": {}} for i in range(stages)],
           "stages": [{"id": f"s{i}", "state": "pending", "job_id": None, "comfy_name": None, "message": None}
                      for i in range(stages)]}
    run["stages"][0].update(state="running", job_id="j0")
    runs.save(run)
    return runs, run


def _ctx(runs, free):
    return pipeline.Context(
        modules=None, runs=runs, jobs=None, info=lambda: None, comfy_url=lambda: "http://x",
        has_input=None, upload=lambda url, data, meta: "stored.png",
        output_path=lambda jid, job: _Out(), free=free)


class _Out:
    def read_bytes(self):
        return b"png"


def _job(state="COMPLETED", idx=0):
    return {"id": f"j{idx}", "state": state, "error": None, "output_file": {"name": "out.png"},
            "provenance": {"pipeline_run_id": "a" * 32, "stage_index": idx, "stage_id": f"s{idx}"}}


def test_flush_runs_before_next_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.uploads, "check", lambda data: {})
    order = []
    monkeypatch.setattr(pipeline, "_launch", lambda ctx, run, idx: order.append(("launch", idx)))
    runs, _ = _run(tmp_path)
    pipeline.on_terminal(_job(idx=0), _ctx(runs, free=lambda url: order.append(("free", url))))
    assert order == [("free", "http://x"), ("launch", 1)]


def test_failed_flush_stops_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.uploads, "check", lambda data: {})
    launched = []
    monkeypatch.setattr(pipeline, "_launch", lambda ctx, run, idx: launched.append(idx))

    def broken(url):
        raise ConnectionError("no server")

    runs, _ = _run(tmp_path)
    pipeline.on_terminal(_job(idx=0), _ctx(runs, free=broken))
    run = runs.get("a" * 32)
    assert run["state"] == "failed"
    assert "could not free ComfyUI memory" in run["message"]
    assert launched == []


def test_last_stage_flushes_then_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.uploads, "check", lambda data: {})
    freed = []
    runs, _ = _run(tmp_path, stages=1)
    pipeline.on_terminal(_job(idx=0), _ctx(runs, free=lambda url: freed.append(url)))
    run = runs.get("a" * 32)
    assert freed == ["http://x"]
    assert run["state"] == "completed"
