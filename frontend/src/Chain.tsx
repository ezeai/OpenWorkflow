import { useEffect, useState } from "react";
import { forgeApi, forgeChainApi, type ChainPort, type ForgeWorkflow, type PipelineRun, type SpecComponent, type SpecView } from "./api";

// A chain is two apps: the first makes an image, the second takes it as its image input.
// Each app becomes a saved module, the modules become a two-step pipeline, and the backend runs it.

export const PORT_TYPE: Partial<Record<SpecComponent["type"], ChainPort["type"]>> = {
  text: "text", prompt: "text", number: "number", slider: "number", seed: "integer",
  select: "select", toggle: "boolean", image_upload: "image",
};

export type Values = Record<string, string | number | boolean | null>;

export interface StepPlan {
  ports: ChainPort[];
  problems: string[];
  outputNode: string | null;
  imageInput: string | null;
}

function portName(id: string, used: Set<string>): string {
  const base = (id.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "").slice(0, 40)) || "input";
  let name = base;
  for (let k = 2; used.has(name); k++) name = `${base.slice(0, 36)}_${k}`;
  used.add(name);
  return name;
}

// Plain-language plan for one app in one role. Pure, so it can be tested without the page.
export function planStep(view: SpecView, role: "first" | "second"): StepPlan {
  const problems: string[] = [];
  const used = new Set<string>();
  const ports: ChainPort[] = [];
  let imageInput: string | null = null;

  for (const c of view.spec.components) {
    if (c.group !== "main" || c.hidden) continue;
    const type = PORT_TYPE[c.type];
    if (!type) continue;
    if (c.type === "image_upload") {
      if (role === "first") {
        problems.push(`"${c.label}" needs its own image, so this app cannot be the first step yet.`);
        continue;
      }
      if (imageInput) continue; // only the first image input receives the result
      imageInput = portName(c.id, used);
      ports.push({ name: imageInput, type, node: c.binding.node, field: c.binding.input, label: c.label, default: null, required: true, options: null });
      continue;
    }
    ports.push({
      name: portName(c.id, used), type, node: c.binding.node, field: c.binding.input, label: c.label,
      default: c.props.default ?? null, required: false,
      options: c.type === "select" ? (c.props.choices ?? []).map(String) : null,
    });
  }

  const image = view.spec.outputs.find((o) => o.media === "image");
  if (role === "first" && !image) problems.push("This app does not save an image, so its result cannot feed a second step.");
  if (role === "second" && !imageInput) problems.push("This app has no image input to receive the first step's result.");
  return { ports, problems, outputNode: image?.node ?? null, imageInput };
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

const STATE_TEXT: Record<string, string> = {
  pending: "Waiting", running: "Running", completed: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
};

interface Picked { id: string; view: SpecView; plan: StepPlan; values: Values }

export function Chain({ apps, onClose }: { apps: ForgeWorkflow[]; onClose?: () => void }) {
  const choices = apps.filter((w) => w.readiness !== "blocked");
  const [firstId, setFirstId] = useState("");
  const [secondId, setSecondId] = useState("");
  const [first, setFirst] = useState<Picked | null>(null);
  const [second, setSecond] = useState<Picked | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);

  const pick = async (role: "first" | "second", id: string) => {
    setError(null);
    if (role === "first") setFirstId(id); else setSecondId(id);
    if (!id) { if (role === "first") setFirst(null); else setSecond(null); return; }
    try {
      const view = await forgeApi.spec(id);
      const plan = planStep(view, role);
      const picked = { id, view, plan, values: defaultsFor(plan.ports) };
      if (role === "first") setFirst(picked); else setSecond(picked);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load that app."); }
  };

  const problems = [...(first?.plan.problems ?? []), ...(second?.plan.problems ?? [])];
  const ready = !!first && !!second && problems.length === 0 && !busy;

  const start = async () => {
    if (!first || !second) return;
    setBusy(true); setError(null); setRun(null); setRunId(null);
    try {
      const name = (w: string) => apps.find((a) => a.id === w)?.name ?? "App";
      const moduleFor = async (p: Picked, role: string) => {
        const outputs = p.plan.outputNode ? [{ name: "image", node: p.plan.outputNode, label: "Image" }] : [];
        const r = await forgeChainApi.createModule({ workflow_id: p.id, name: `Chain ${role}: ${name(p.id)}`, inputs: p.plan.ports, outputs });
        return r.module.module_id;
      };
      const m1 = await moduleFor(first, "step 1");
      const m2 = await moduleFor(second, "step 2");
      const values2: Record<string, unknown> = { ...second.values };
      if (second.plan.imageInput) values2[second.plan.imageInput] = { from_stage: "first", output: "image" };
      const p = await forgeChainApi.createPipeline({
        name: `${name(first.id)} then ${name(second.id)}`,
        stages: [
          { id: "first", module_id: m1, version: 1, values: first.values },
          { id: "second", module_id: m2, version: 1, values: values2 },
        ],
      });
      const started = await forgeChainApi.runPipeline(p.pipeline.id);
      setRunId(started.run.id);
      setRun(started.run);
    } catch (e) { setError(e instanceof Error ? e.message : "The chain could not start."); }
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

  const resultJob = run?.state === "completed" ? run.stages[run.stages.length - 1]?.job_id : null;

  return (
    <section className="card chain" aria-labelledby="chain-h">
      <header className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h2 id="chain-h">Chain two apps</h2>
          <p className="muted">The first app makes an image. The second app uses it as its input. Each step runs on this computer and keeps its own record.</p>
        </div>
        {onClose && <button className="secondary" onClick={onClose}>Close</button>}
      </header>

      {choices.length < 2 && <p className="muted" role="status">You need two apps that are not blocked before you can chain them.</p>}

      <div className="chain-steps">
        <label className="chain-step">
          <span>Step 1: app that makes the image</span>
          <select value={firstId} onChange={(e) => void pick("first", e.target.value)} disabled={busy}>
            <option value="">Choose an app</option>
            {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <label className="chain-step">
          <span>Step 2: app that takes the image</span>
          <select value={secondId} onChange={(e) => void pick("second", e.target.value)} disabled={busy}>
            <option value="">Choose an app</option>
            {choices.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
      </div>

      {error && <div className="banner bad" role="alert">{error}</div>}
      {problems.length > 0 && (
        <ul className="chain-problems" aria-label="What to fix">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}

      {first && first.plan.problems.length === 0 && <StepFields title="Step 1 settings" picked={first} onChange={(v) => setFirst({ ...first, values: v })} />}
      {second && second.plan.problems.length === 0 && (
        <StepFields title="Step 2 settings" picked={second} onChange={(v) => setSecond({ ...second, values: v })} note="Receives the image from step 1." />
      )}

      <div className="actions">
        <button onClick={() => void start()} disabled={!ready}>{busy ? "Starting…" : "Run chain"}</button>
      </div>

      {run && (
        <div className="chain-run" role="status" aria-live="polite">
          <p><strong>{run.state === "running" ? "Running" : run.state === "completed" ? "Chain finished" : "Chain stopped"}</strong>
            {run.message ? ` · ${run.message}` : ""}</p>
          <ol>
            {run.stages.map((s, i) => (
              <li key={s.id}>Step {i + 1}: {STATE_TEXT[s.state] ?? s.state}{s.message ? ` (${s.message})` : ""}</li>
            ))}
          </ol>
          {resultJob && <img className="chain-result" src={forgeApi.runOutputUrl(resultJob)} alt="Final result of the chain" />}
        </div>
      )}
    </section>
  );
}

function StepFields({ title, picked, onChange, note }: { title: string; picked: Picked; onChange: (v: Values) => void; note?: string }) {
  const visible = picked.plan.ports.filter((p) => p.type !== "image");
  if (visible.length === 0 && !note) return null;
  const setField = (name: string, value: string | boolean) => {
    const port = picked.plan.ports.find((p) => p.name === name);
    if (port) onChange({ ...picked.values, [name]: coerce(port, value) });
  };
  return (
    <fieldset className="chain-fields">
      <legend>{title}</legend>
      {note && <p className="muted">{note}</p>}
      {visible.map((p) => {
        const id = `chain-${picked.id}-${p.name}`;
        const value = picked.values[p.name];
        if (p.type === "boolean") {
          return (
            <label key={p.name} className="check"><input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => setField(p.name, e.target.checked)} /> {p.label}</label>
          );
        }
        if (p.type === "select") {
          return (
            <label key={p.name} htmlFor={id}>{p.label}
              <select id={id} value={String(value ?? "")} onChange={(e) => setField(p.name, e.target.value)}>
                {(p.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          );
        }
        if (p.type === "text") {
          return (
            <label key={p.name} htmlFor={id}>{p.label}
              <textarea id={id} rows={2} value={String(value ?? "")} onChange={(e) => setField(p.name, e.target.value)} />
            </label>
          );
        }
        return (
          <label key={p.name} htmlFor={id}>{p.label}
            <input id={id} type="number" value={value === null || value === undefined ? "" : String(value)} onChange={(e) => setField(p.name, e.target.value)} />
          </label>
        );
      })}
    </fieldset>
  );
}
