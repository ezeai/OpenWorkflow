"""Live two-stage pipeline check: Z-Image Turbo (text to image) -> 4xNomos8kDAT upscale (CC-BY-4.0). Local Forge API only."""
import httpx, json, sys, time, hashlib
B = "http://127.0.0.1:8766/api"
F = B + "/forge"
zwid = sys.argv[1]
up = {"1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
      "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xNomos8kDAT.pth"}},
      "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
      "4": {"class_type": "ImageScaleBy", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "scale_by": 0.5}},
      "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "pipe-upscale"}}}
r = httpx.post(F + "/workflows", params={"name": "pipe-test-upscale"}, content=json.dumps(up).encode(), timeout=60); print("import up", r.status_code, r.text[:200]); r.raise_for_status()
uwid = r.json()["workflow"]["id"]
def mod(body):
    r = httpx.post(F + "/modules", json=body, timeout=60); print("module", r.status_code, r.text[:400]); r.raise_for_status(); return r.json()
m1 = mod({"workflow_id": zwid, "name": "Draft image", "inputs": [
    {"name": "prompt", "type": "text", "node": "57:27", "field": "text", "required": True},
    {"name": "seed", "type": "integer", "node": "57:3", "field": "seed", "default": 7},
    {"name": "width", "type": "integer", "node": "57:13", "field": "width", "default": 512},
    {"name": "height", "type": "integer", "node": "57:13", "field": "height", "default": 512}],
    "outputs": [{"name": "image", "node": "9"}]})
m2 = mod({"workflow_id": uwid, "name": "Upscale 2x", "inputs": [
    {"name": "image", "type": "image", "node": "1", "field": "image", "required": True}],
    "outputs": [{"name": "image", "node": "5"}]})
def mid(m): return (m.get("module") or m)["module_id"]
spec = {"name": "Draft then upscale", "stages": [
    {"id": "draft", "module_id": mid(m1), "version": 1, "values": {"prompt": "A lighthouse on a cliff at sunset, cinematic", "seed": 42, "width": 512, "height": 512}},
    {"id": "upscale", "module_id": mid(m2), "version": 1, "values": {"image": {"from_stage": "draft", "output": "image"}}}]}
r = httpx.post(F + "/pipelines", json=spec, timeout=60); print("pipeline", r.status_code, r.text[:300]); r.raise_for_status()
pid = (r.json().get("pipeline") or r.json())["id"]
r = httpx.post(F + f"/pipelines/{pid}/validate", timeout=60); print("validate", r.status_code, r.text[:300])
t = time.time()
r = httpx.post(F + f"/pipelines/{pid}/run", timeout=60); print("run", r.status_code, r.text[:400]); r.raise_for_status()
rid = (r.json().get("run") or r.json())["id"]
while True:
    run = httpx.get(F + f"/pipeline-runs/{rid}", timeout=30).json(); run = run.get("run") or run
    if run["state"] != "running" or time.time() - t > 900: break
    time.sleep(3)
print("elapsed", round(time.time() - t, 1)); print(json.dumps(run, indent=1)[:3000])
for st in run["stages"]:
    if st["job_id"]:
        j = httpx.get(B + f"/jobs/{st['job_id']}", timeout=30).json()
        print(st["id"], j["state"], j.get("output_file"), json.dumps(j.get("provenance"))[:600])
        o = httpx.get(F + f"/runs/{st['job_id']}/output", timeout=60)
        if o.status_code == 200:
            from PIL import Image; import io
            im = Image.open(io.BytesIO(o.content)); print("  output", im.size, hashlib.sha256(o.content).hexdigest()[:16])
            open(sys.argv[2] + f"/{st['id']}.png", "wb").write(o.content)
