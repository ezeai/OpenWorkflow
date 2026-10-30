"""Gate G2: three live two-stage runs (different seeds) and one forced failure (stage 1 cancelled).
Usage: python gate.py <backend url> <out dir>. Uses the modules already made by pipe_test.py on that backend."""
import hashlib, io, json, sys, time
import httpx
from PIL import Image
B, OUT = sys.argv[1].rstrip("/") + "/api", sys.argv[2]
F = B + "/forge"
mods = httpx.get(F + "/modules", timeout=30).json()
mods = mods.get("modules", mods)
def latest(name): return max((m for m in mods if m["name"] == name), key=lambda m: m.get("created_at", ""))["module_id"]
draft, up = latest("Draft image"), latest(sys.argv[3] if len(sys.argv) > 3 else "Upscale 2x")
PROMPTS = ["A lighthouse on a cliff at sunset, cinematic", "A red fox in fresh snow, morning light", "A neon street market at night, rain"]

def make(seed, prompt):
    spec = {"name": f"Gate seed {seed}", "stages": [
        {"id": "draft", "module_id": draft, "version": 1, "values": {"prompt": prompt, "seed": seed, "width": 512, "height": 512}},
        {"id": "upscale", "module_id": up, "version": 1, "values": {"image": {"from_stage": "draft", "output": "image"}}}]}
    return httpx.post(F + "/pipelines", json=spec, timeout=30).json()["pipeline"]["id"]

def wait(rid, cancel_first=False):
    while True:
        run = httpx.get(F + f"/pipeline-runs/{rid}", timeout=30).json()["run"]
        if cancel_first and run["stages"][0]["job_id"]:
            print("  cancel", httpx.post(B + f"/jobs/{run['stages'][0]['job_id']}/cancel", timeout=30).status_code)
            cancel_first = False
        if run["state"] != "running": return run
        time.sleep(2)

results = []
for n, seed in enumerate([101, 202, 303], 1):
    t = time.time(); rid = httpx.post(F + f"/pipelines/{make(seed, PROMPTS[n-1])}/run", timeout=60).json()["run"]["id"]
    run = wait(rid); row = {"run": n, "seed": seed, "state": run["state"], "message": run["message"], "seconds": round(time.time() - t, 1), "stages": []}
    for st in run["stages"]:
        s = {"id": st["id"], "state": st["state"], "message": st["message"]}
        o = httpx.get(F + f"/runs/{st['job_id']}/output", timeout=60) if st["job_id"] else None
        if o is not None and o.status_code == 200:
            s["size"] = Image.open(io.BytesIO(o.content)).size; s["sha256"] = hashlib.sha256(o.content).hexdigest()
            open(f"{OUT}/gate-{n}-{st['id']}.png", "wb").write(o.content)
        rc = httpx.get(F + f"/runs/{st['job_id']}/receipt", timeout=30) if st["job_id"] else None
        if rc is not None and rc.status_code == 200:
            r = rc.json(); s["receipt"] = {k: r.get(k) for k in ("pipeline_run_id", "stage_index", "stage_id", "module_version", "snapshot_sha256", "values")}
        row["stages"].append(s)
    print(json.dumps(row)); results.append(row)

rid = httpx.post(F + f"/pipelines/{make(404, PROMPTS[0])}/run", timeout=60).json()["run"]["id"]
run = wait(rid, cancel_first=True)
fail = {"run": "forced-failure", "state": run["state"], "message": run["message"], "stages": [(s["id"], s["state"]) for s in run["stages"]]}
print(json.dumps(fail)); results.append(fail)
json.dump(results, open(f"{OUT}/gate-results.json", "w"), indent=1)
