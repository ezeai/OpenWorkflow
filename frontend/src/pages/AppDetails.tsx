import { useCallback, useEffect, useState } from "react";
import { api, forgeApi, type ForgeAnalysis, type ForgeWorkflow, type Hardware } from "../api";
import { AppCover } from "../AppCover";
import { MODEL_STATUS, READINESS, gpuFit, toneFor } from "../Apps";
import { EmptyState, Icon, StatusBadge, type Tone } from "../ui/primitives";

const MODEL_TONE: Record<string, Tone> = { available: "good", found_elsewhere: "good", ambiguous: "warn", missing: "bad", unknown: "neutral" };
const DEP_TEXT: Record<string, { text: string; tone: Tone }> = {
  core: { text: "Built into ComfyUI", tone: "good" },
  registered: { text: "Installed", tone: "good" },
  unchecked: { text: "Not checked (start ComfyUI)", tone: "neutral" },
  missing: { text: "Missing", tone: "bad" },
  candidate: { text: "Missing, pack found", tone: "warn" },
  choose: { text: "Missing, choose a pack", tone: "warn" },
};
const GB = 1024 ** 3;

/**
 * Everything an app needs before it runs, from the same analysis the app's Analysis tab uses.
 * Nothing here is estimated: model and node states come from the backend, VRAM from the hardware probe.
 */
export function AppDetails({ id, onBack, onRun, onConfigure }: { id: string; onBack: () => void; onRun: () => void; onConfigure: () => void }) {
  const [data, setData] = useState<{ workflow: ForgeWorkflow; analysis: ForgeAnalysis } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hw, setHw] = useState<Hardware | null>(null);

  const load = useCallback(() => {
    setData(null); setErr(null);
    forgeApi.analysis(id).then(setData).catch((e) => setErr(e instanceof Error ? e.message : "This app could not be checked."));
  }, [id]);
  useEffect(load, [load]);
  useEffect(() => { api.hardware().then(setHw).catch(() => setHw(null)); }, []);

  if (err) return (
    <div className="page wide">
      <button type="button" className="link back-link" onClick={onBack}>← All apps</button>
      <div className="banner bad" role="alert">{err}</div>
      <button className="secondary" onClick={load}>Try again</button>
    </div>
  );
  if (!data) return <div className="page wide" aria-busy="true"><div className="skeleton" style={{ height: 260 }} /></div>;

  const { workflow: w, analysis: a } = data;
  const r = READINESS[a.readiness];
  const gpu = hw?.gpus.reduce<Hardware["gpus"][number] | null>((m, g) => (!m || g.vram_total_mb > m.vram_total_mb ? g : m), null) ?? null;
  const fit = gpuFit(a.weights, gpu);
  const deps = a.dependencies?.requirements ?? [];
  const custom = deps.filter((d) => d.status !== "core");
  const inputs = a.controls.filter((c) => c.suggested.show && !c.suggested.hidden);
  const blocking = a.problems.filter((p) => p.severity === "error");
  const warnings = a.problems.filter((p) => p.severity === "warning");

  return (
    <div className="page wide app-details">
      <button type="button" className="link back-link" onClick={onBack}>← All apps</button>

      <section className="details-hero" aria-labelledby="details-h">
        <AppCover id={w.id} tone={toneFor(w.name)} name={w.name} />
        <div className="details-intro">
          <p className="eyebrow">App details</p>
          <h1 id="details-h">{w.name}</h1>
          <div className="pills">
            <StatusBadge tone={r.cls as Tone}>{r.text}</StatusBadge>
            <span className="status-badge neutral" title={`Inferred from ${a.task.rule}`}>{a.task.family.replace(/-/g, " ")}</span>
            <span className="status-badge neutral">{w.origin.startsWith("comfyui-template:") ? "ComfyUI template" : "Imported workflow"}</span>
          </div>
          {a.reasons.length > 0 && <ul className="reasons">{a.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
          <div className="actions">
            <button className="btn-primary" onClick={onRun} disabled={a.readiness === "blocked"}><Icon name="play" size={16} />{a.readiness === "blocked" ? "Blocked" : "Run app"}</button>
            <button className="secondary" onClick={onConfigure}>Configure</button>
          </div>
          {a.readiness === "blocked" && <p className="small warn-text">Resolve the blocking items below before this app can run.</p>}
        </div>
      </section>

      <div className="details-grid">
        <section className="details-card" aria-labelledby="io-h">
          <h2 id="io-h" className="section-title">Inputs and outputs</h2>
          <h3 className="details-sub">Main controls ({inputs.length})</h3>
          {inputs.length === 0 ? <p className="muted small">No main controls were found.</p> : (
            <ul className="tag-list">{inputs.map((c) => <li key={c.id}>{c.node_title ?? c.input}<span className="muted"> · {c.inferred.kind}</span></li>)}</ul>
          )}
          <h3 className="details-sub">Outputs ({a.outputs.length})</h3>
          {a.outputs.length === 0 ? <p className="muted small">No output node was found.</p> : (
            <ul className="tag-list">{a.outputs.map((o) => <li key={o.node}>{o.title ?? o.class_type}<span className="muted"> · {o.media}</span></li>)}</ul>
          )}
        </section>

        <section className="details-card" aria-labelledby="hw-h">
          <h2 id="hw-h" className="section-title">Hardware</h2>
          <dl className="readiness-list">
            <div><dt>GPU</dt><dd>{!hw ? <span className="muted">Checking…</span> : gpu ? gpu.name : <span className="muted">No NVIDIA GPU detected</span>}</dd></div>
            <div><dt>VRAM</dt><dd>{gpu ? `${(gpu.vram_free_mb / 1024).toFixed(1)} of ${(gpu.vram_total_mb / 1024).toFixed(0)} GB free` : <span className="muted">Not measured</span>}</dd></div>
            <div><dt>Model files</dt><dd>{a.weights.measured > 0 ? `${(a.weights.total_bytes / GB).toFixed(1)} GB measured${a.weights.unmeasured ? `, ${a.weights.unmeasured} not measured` : ""}` : <span className="muted">Not measured yet</span>}</dd></div>
            <div><dt>Fit</dt><dd>{fit ? <StatusBadge tone={fit.tone === "muted" ? "neutral" : fit.tone}>{fit.text}</StatusBadge> : <span className="muted">Nothing to compare yet</span>}</dd></div>
          </dl>
          <p className="small muted">The fit compares file sizes with VRAM only. A run also needs working memory, so a fit is not a guarantee.</p>
        </section>

        <section className="details-card wide" aria-labelledby="models-h">
          <h2 id="models-h" className="section-title">Models ({a.model_files.length})</h2>
          {a.model_files.length === 0 ? <EmptyState icon="library" title="No model files">This workflow does not load model files by name.</EmptyState> : (
            <ul className="req-list">
              {a.model_files.map((m) => (
                <li key={m.id}>
                  <span className="min0"><span className="path ellipsis" title={m.file}>{m.file}</span><span className="small muted">{m.class_type}</span></span>
                  <StatusBadge tone={MODEL_TONE[m.status]}>{MODEL_STATUS[m.status]}</StatusBadge>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="details-card wide" aria-labelledby="deps-h">
          <h2 id="deps-h" className="section-title">Custom nodes ({custom.length})</h2>
          {a.dependencies?.headline && <p className="small muted">{a.dependencies.headline}</p>}
          {custom.length === 0 ? <p className="muted small">Only ComfyUI's built-in nodes are used.</p> : (
            <ul className="req-list">
              {custom.map((d) => {
                const s = DEP_TEXT[d.status] ?? { text: d.status, tone: "neutral" as Tone };
                return (
                  <li key={d.class}>
                    <span className="min0"><span className="path ellipsis">{d.class}</span>
                      {d.candidates[0] && <span className="small muted ellipsis">Pack: {d.candidates[0].title ?? d.candidates[0].repository}</span>}</span>
                    <StatusBadge tone={s.tone}>{s.text}</StatusBadge>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {(blocking.length > 0 || warnings.length > 0) && (
          <section className="details-card wide" aria-labelledby="probs-h">
            <h2 id="probs-h" className="section-title">Problems</h2>
            <ul className="req-list">
              {[...blocking, ...warnings].map((p, i) => (
                <li key={i}><span>{p.message}</span><StatusBadge tone={p.severity === "error" ? "bad" : "warn"}>{p.severity === "error" ? "Blocking" : "Warning"}</StatusBadge></li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
