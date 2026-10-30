import { useEffect, useState } from "react";

type ModelRow = { kind: string; file: string; loaded_by: string[]; needed: boolean };
type SearchResult = { available: boolean; source: string; count: number; items: ModelRow[]; folders: string[] };

const LABEL: Record<string, string> = {
  loras: "LoRA", checkpoints: "Checkpoint", diffusion_models: "Diffusion model", vae: "VAE",
  text_encoders: "Text encoder", upscale_models: "Upscaler", controlnet: "ControlNet",
};

/** Search the models ComfyUI can load. "Needed by this app" marks the files the workflow uses. */
export function ModelSearch({ workflowId }: { workflowId: string }) {
  const [q, setQ] = useState("");
  const [folder, setFolder] = useState("");
  const [neededOnly, setNeededOnly] = useState(false);
  const [data, setData] = useState<SearchResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const params = new URLSearchParams({ workflow: workflowId });
    if (q.trim()) params.set("q", q.trim());
    if (folder) params.set("folder", folder);
    const t = window.setTimeout(() => {
      fetch(`/api/forge/models?${params}`)
        .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "The model list could not be read."); return r.json(); })
        .then((d: SearchResult) => { if (alive) { setData(d); setErr(null); } })
        .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "The model list could not be read."); });
    }, 200);   // a short pause while typing, so each keystroke is not a request
    return () => { alive = false; window.clearTimeout(t); };
  }, [workflowId, q, folder]);

  const rows = (data?.items ?? []).filter((r) => !neededOnly || r.needed);
  return (
    <section className="model-search" aria-labelledby="ms-h">
      <h3 id="ms-h" className="sub-h">Models ComfyUI can load</h3>
      <div className="model-tools">
        <input type="search" aria-label="Search models" placeholder="Search by name, e.g. anime or detail" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Model folder" value={folder} onChange={(e) => setFolder(e.target.value)}>
          <option value="">All folders</option>
          {(data?.folders ?? Object.keys(LABEL)).map((f) => <option key={f} value={f}>{LABEL[f] ?? f}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={neededOnly} onChange={(e) => setNeededOnly(e.target.checked)} /> Needed by this app</label>
      </div>
      {err && <p className="warn-text" role="alert">{err}</p>}
      {data && !data.available && <p className="muted small">ComfyUI is not running, so its model list cannot be read. Start ComfyUI, then search again.</p>}
      {data?.available && (
        <>
          <p className="small muted" role="status">{rows.length} of {data.count} shown{neededOnly ? ", needed by this app" : ""}</p>
          <ul className="model-list">
            {rows.map((r) => (
              <li key={`${r.kind}:${r.file}`} className="model-row">
                <span className={`pill ${r.needed ? "good" : "muted"}`}>{r.needed ? "Needed" : LABEL[r.kind] ?? r.kind}</span>
                <code className="model-file">{r.file}</code>
                <span className="small muted">{LABEL[r.kind] ?? r.kind} · loaded by {r.loaded_by.join(", ")}</span>
              </li>
            ))}
          </ul>
          {rows.length === 0 && <p className="muted small">No model matches. Check the spelling, or pick another folder.</p>}
        </>
      )}
    </section>
  );
}
