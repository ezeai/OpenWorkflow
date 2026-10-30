import { useEffect, useState } from "react";
import { forgeApi, forgeChainApi, forgeUploadApi, type ChainPort, type ForgeWorkflow, type PipelineRun, type SpecView } from "./api";
import { PORT_TYPE, type Values } from "./Chain";

// A flow runs several apps one after another on this computer. Each step is an app with its own settings.
// A step can take an image from the step before it, or from an image the user uploads. Video results are shown as video.

const STATE_TEXT: Record<string, string> = {
  pending: "Waiting", running: "Running", completed: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
};

export interface FlowStep {
  appId: string;
  view: SpecView | null;
  ports: ChainPort[];
  output: { node: string; media: "image" | "video" } | null;
  values: Values;
  images: Record<string, string>; // image port name -> the name ComfyUI stores the upload under
  feedPrevious: boolean;          // the first image port takes the previous step's image result
  problems: string[];
}

function safeName(id: string, used: Set<string>): string {
  const base = (id.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "").slice(0, 40)) || "input";
  let name = base;
  for (let k = 2; used.has(name); k++) name = `${base.slice(0, 36)}_${k}`;
  used.add(name);
  return name;
}

// The ports and output of one app, for use as a flow step. Every image input is its own port.
export function planFlowStep(view: SpecView): Pick<FlowStep, "ports" | "output" | "problems"> {
  const problems: string[] = [];
  const used = new Set<string>();
  const ports: ChainPort[] = [];
  for (const c of view.spec.components) {
    if (c.group !== "main" || c.hidden) continue;
    const type = PORT_TYPE[c.type];
    if (!type) continue;
    if (c.type === "image_upload") {
      ports.push({ name: safeName(c.id, used), type: "image", node: c.binding.node, field: c.binding.input, label: c.label, default: null, required: true, options: null });
      continue;
    }
    ports.push({
      name: safeName(c.id, used), type, node: c.binding.node, field: c.binding.input, label: c.label,
      default: c.props.default ?? null, required: false,
      options: c.type === "select" ? (c.props.choices ?? []).map(String) : null,
    });
  }
  const out = view.spec.outputs.find((o) => o.media === "image" || o.media === "video");
  if (!out) problems.push("This app does not save an image or a video, so it cannot be a flow step.");
  const media = out?.media === "video" ? "video" : "image";
  return { ports, output: out ? { node: out.node, media } : null, problems };
}

function defaultsFor(ports: ChainPort[]): Values {
  const v: Values = {};
  for (const p of ports) if (p.type !== "image") v[p.name] = p.default;
  return v;
}

function coerce(p: ChainPort, raw: string | boolean): string | number | boolean | null {
  if (p.type === "boolean") return Boolean(raw);
  if (p.type === "number" || p.type === "integer") {
    const n = Number(raw);
    return raw === "" || Number.isNaN(n) ? null : p.type === "integer" ? Math.trunc(n) : n;
  }
  return String(raw);
}

export function emptyStep(): FlowStep {
  return { appId: "", view: null, ports: [], output: null, values: {}, images: {}, feedPrevious: true, problems: [] };
}

// What still stops a step from running. Pure, so it can be tested without the page.
export function stepProblems(step: FlowStep, index: number, steps: FlowStep[]): string[] {
  if (!step.appId) return ["Choose an app for this step."];
  const out = [...step.problems];
  const previous = index > 0 ? steps[index - 1] : null;
  const fed = step.feedPrevious && previous?.output?.media === "image";
  let fedOnce = false;
  for (const p of step.ports) {
    if (p.type === "image") {
      if (fed && !fedOnce) { fedOnce = true; continue; }
      if (!step.images[p.name]) out.push(`"${p.label}" needs an uploaded image.`);
    } else if (p.required && (step.values[p.name] === null || step.values[p.name] === undefined || step.values[p.name] === "")) {
      out.push(`"${p.label}" needs a value.`);
    }
  }
  return out;
}

export function buildStages(steps: FlowStep[]): { id: string; module_id: string; version: number; values: Record<string, unknown> }[] {
  return steps.map((s, i) => {
    const values: Record<string, unknown> = { ...s.values };
    const previous = i > 0 ? steps[i - 1] : null;
    const fed = s.feedPrevious && previous?.output?.media === "image";
    let fedOnce = false;
    for (const p of s.ports) {
      if (p.type !== "image") continue;
      if (fed && !fedOnce) { fedOnce = true; values[p.name] = { from_stage: `s${i}`, output: "result" }; continue; }
      values[p.name] = s.images[p.name];
    }
    return { id: `s${i + 1}`, module_id: "", version: 1, values };
  });
}

export function Flow({ apps, onClose }: { apps: ForgeWorkflow[]; onClose?: () => void }) {
  const choices = apps.filter((w) => w.readiness !== "blocked");
  const [steps, setSteps] = useState<FlowStep[]>([emptyStep(), emptyStep()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const update = (i: number, next: Partial<FlowStep>) => setSteps((all) => all.map((s, j) => (j === i ? { ...s, ...next } : s)));

  const pick = async (i: number, id: string) => {
    setError(null);
    if (!id) { update(i, emptyStep()); return; }
    try {
      const view = await forgeApi.spec(id);
      const plan = planFlowStep(view);
      update(i, { appId: id, view, ports: plan.ports, output: plan.output, values: defaultsFor(plan.ports), images: {}, feedPrevious: true, problems: plan.problems });
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load that app."); }
  };

  const upload = async (i: number, port: ChainPort, file: File) => {
    setError(null);
    try {
      const r = await forgeUploadApi.upload(file);
      update(i, { images: { ...steps[i].images, [port.name]: r.name } });
    } catch (e) { setError(e instanceof Error ? e.message : "The image could not be uploaded."); }
  };

  const addStep = () => setSteps((all) => [...all, emptyStep()]);
  const removeStep = (i: number) => setSteps((all) => all.filter((_, j) => j !== i));
  const move = (i: number, d: number) => setSteps((all) => {
    const j = i + d;
    if (j < 0 || j >= all.length) return all;
    const next = [...all];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });

  const problems = steps.flatMap((s, i) => stepProblems(s, i, steps).map((p) => `Step ${i + 1}: ${p}`));
  const ready = problems.length === 0 && !busy && steps.length > 0;

  const start = async () => {
    setBusy(true); setError(null); setRun(null); setRunId(null);
    try {
      const modules: string[] = [];
      for (let i = 0; i < steps.length; i++) {
        const s = steps[i];
        const name = choices.find((w) => w.id === s.appId)?.name ?? "App";
        const r = await forgeChainApi.createModule({
          workflow_id: s.appId, name: `Flow step ${i + 1}: ${name}`, inputs: s.ports,
          outputs: s.output ? [{ name: "result", node: s.output.node, label: "Result" }] : [],
        });
        modules.push(r.module.module_id);
      }
      const stages = buildStages(steps).map((st, i) => ({ ...st, module_id: modules[i] }));
      const names = steps.map((s) => choices.find((w) => w.id === s.appId)?.name ?? "App");
      const p = await forgeChainApi.createPipeline({ name: names.join(" then "), stages });
      const started = await forgeChainApi.runPipeline(p.pipeline.id);
      setRunId(started.run.id);
      setRun(started.run);
    } catch (e) { setError(e instanceof Error ? e.message : "The flow could not start."); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const { run: next } = await forgeChainApi.pipelineRun(runId);
        if (!alive) return;
        setRun(next);
        if (next.state === "running") timer = setTimeout(tick, 2000);
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "Lost contact with the run."); }
    };
    void tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [runId]);

  const last = run?.state === "completed" ? run.stages[run.stages.length - 1] : null;
  const lastStep = steps[steps.length - 1];
  const resultUrl = last?.job_id ? forgeApi.runOutputUrl(last.job_id) : null;

  return (
    <section className="card chain" aria-labelledby="flow-h">
      <header className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h2 id="flow-h">Run a flow</h2>
          <p className="muted">Apps run one after another, each on this computer. A step takes the image before it, or an image you upload. Memory is freed after every step.</p>
        </div>
        {onClose && <button className="secondary" onClick={onClose}>Close</button>}
      </header>

      {choices.length < 1 && <p className="muted" role="status">You need an app that is not blocked before you can run a flow.</p>}

      {steps.map((s, i) => (
        <fieldset key={i} className="chain-fields">
          <legend>Step {i + 1}</legend>
          <label>App
            <select value={s.appId} onChange={(e) => void pick(i, e.target.value)} disabled={busy}>
              <option value="">Choose an app</option>
              {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          {s.view && s.ports.filter((p) => p.type !== "image").map((p) => {
            const id = `flow-${i}-${p.name}`;
            const value = s.values[p.name];
            if (p.type === "boolean") {
              return <label key={p.name} className="check"><input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => update(i, { values: { ...s.values, [p.name]: coerce(p, e.target.checked) } })} /> {p.label}</label>;
            }
            if (p.type === "select") {
              return (
                <label key={p.name} htmlFor={id}>{p.label}
                  <select id={id} value={String(value ?? "")} onChange={(e) => update(i, { values: { ...s.values, [p.name]: coerce(p, e.target.value) } })}>
                    {(p.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
              );
            }
            if (p.type === "text") {
              return (
                <label key={p.name} htmlFor={id}>{p.label}
                  <textarea id={id} rows={2} value={String(value ?? "")} onChange={(e) => update(i, { values: { ...s.values, [p.name]: coerce(p, e.target.value) } })} />
                </label>
              );
            }
            return (
              <label key={p.name} htmlFor={id}>{p.label}
                <input id={id} type="number" value={value === null || value === undefined ? "" : String(value)} onChange={(e) => update(i, { values: { ...s.values, [p.name]: coerce(p, e.target.value) } })} />
              </label>
            );
          })}
          {s.view && s.ports.some((p) => p.type === "image") && (
            <div className="flow-images">
              {i > 0 && steps[i - 1].output?.media === "image" && (
                <label className="check"><input type="checkbox" checked={s.feedPrevious} onChange={(e) => update(i, { feedPrevious: e.target.checked })} /> Use the image from step {i}</label>
              )}
              {s.ports.filter((p) => p.type === "image").map((p, k) => {
                const fed = s.feedPrevious && i > 0 && steps[i - 1].output?.media === "image" && k === 0;
                if (fed) return <p key={p.name} className="muted">{p.label}: from step {i}</p>;
                return (
                  <label key={p.name}>{p.label}
                    <input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy}
                      onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(i, p, f); }} />
                    {s.images[p.name] ? <span className="muted"> Uploaded</span> : null}
                  </label>
                );
              })}
            </div>
          )}
          <div className="actions">
            {i > 0 && <button className="secondary" onClick={() => move(i, -1)} disabled={busy}>Move up</button>}
            {i < steps.length - 1 && <button className="secondary" onClick={() => move(i, 1)} disabled={busy}>Move down</button>}
            {steps.length > 1 && <button className="secondary" onClick={() => removeStep(i)} disabled={busy}>Remove step</button>}
          </div>
        </fieldset>
      ))}

      <div className="actions">
        <button className="secondary" onClick={addStep} disabled={busy}>Add step</button>
      </div>

      {error && <div className="banner bad" role="alert">{error}</div>}
      {problems.length > 0 && (
        <ul className="chain-problems" aria-label="What to fix">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}

      <div className="actions">
        <button onClick={() => void start()} disabled={!ready}>{busy ? "Starting…" : "Run flow"}</button>
      </div>

      {run && (
        <div className="chain-run" role="status" aria-live="polite">
          <p><strong>{run.state === "running" ? "Running" : run.state === "completed" ? "Flow finished" : "Flow stopped"}</strong>
            {run.message ? ` · ${run.message}` : ""}</p>
          <ol>
            {run.stages.map((s, i) => (
              <li key={s.id}>Step {i + 1}: {STATE_TEXT[s.state] ?? s.state}{s.message ? ` (${s.message})` : ""}</li>
            ))}
          </ol>
          {resultUrl && lastStep?.output?.media === "video" && (
            <video className="chain-result" src={resultUrl} controls aria-label="Final result of the flow" />
          )}
          {resultUrl && lastStep?.output?.media !== "video" && (
            <img className="chain-result" src={resultUrl} alt="Final result of the flow" />
          )}
        </div>
      )}
    </section>
  );
}
