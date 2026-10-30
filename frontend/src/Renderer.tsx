import { useId } from "react";
import type { ComponentVerdict, SpecComponent, UISpec } from "./api";

export type Values = Record<string, string | number | boolean>;

// JavaScript numbers are exact only below 2^53, so random seeds stay below it even where ComfyUI allows more.
const MAX_SAFE_SEED = Number.MAX_SAFE_INTEGER;

export function defaultValues(spec: UISpec): Values {
  const v: Values = {};
  for (const c of spec.components) {
    const d = c.props.default;
    if (d !== null && d !== undefined) v[c.id] = d;
    else if (c.type === "toggle") v[c.id] = false;
    else if (c.type === "text" || c.type === "prompt") v[c.id] = "";
  }
  return v;
}

function Field({ c, verdict, value, onChange, onUpload }: { c: SpecComponent; verdict?: ComponentVerdict; value: Values[string] | undefined; onChange: (v: Values[string]) => void; onUpload?: (file: File) => Promise<string> }) {
  const id = useId();
  const note = verdict?.status === "unresolved" ? verdict.reasons[0] : null;
  const p = c.props;
  let control: React.ReactNode;
  switch (c.type) {
    case "prompt":
      control = <textarea id={id} rows={p.rows ?? 4} value={String(value ?? "")} placeholder={p.placeholder ?? ""} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "text":
      control = <input id={id} type="text" value={String(value ?? "")} placeholder={p.placeholder ?? ""} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "number":
      control = <input id={id} type="number" min={p.min ?? undefined} max={p.max ?? undefined} step={p.step ?? "any"} value={value === undefined ? "" : Number(value)}
        onChange={(e) => e.target.value !== "" && onChange(Number(e.target.value))} />;
      break;
    case "slider":
      control = (
        <span className="slider-row">
          <input id={id} type="range" min={p.min ?? 0} max={p.max ?? 1} step={p.step ?? "any"} value={Number(value ?? p.min ?? 0)} onChange={(e) => onChange(Number(e.target.value))} />
          <output htmlFor={id}>{String(value ?? "")}</output>
        </span>
      );
      break;
    case "seed":
      control = (
        <span className="slider-row">
          <input id={id} type="number" min={p.min ?? 0} step={1} value={value === undefined ? "" : Number(value)} onChange={(e) => e.target.value !== "" && onChange(Math.trunc(Number(e.target.value)))} />
          <button type="button" className="secondary" onClick={() => onChange(Math.floor(Math.random() * MAX_SAFE_SEED))}>Randomize</button>
        </span>
      );
      break;
    case "select":
      control = (p.choices && p.choices.length > 0) ? (
        <select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {p.choices.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
        </select>
      ) : <input id={id} type="text" value={String(value ?? "")} disabled aria-describedby={`${id}-n`} />;
      break;
    case "toggle":
      control = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
      break;
    case "image_upload":
      control = (
        <span className="upload-row">
          <input id={id} type="file" accept="image/png,image/jpeg,image/webp" disabled={!onUpload}
            onChange={(e) => { const f = e.target.files?.[0]; if (f && onUpload) void onUpload(f).then((n) => onChange(n)).catch(() => undefined); }} />
          {value ? <span className="small ok">Ready: {String(value)}</span> : <span className="small muted">No image yet</span>}
        </span>
      );
      break;
  }
  const disabledNote = c.type === "image_upload" && !onUpload ? "Images can be added when the app is running." : null;
  return (
    <div className="r-field">
      <label htmlFor={id}>{c.label}</label>
      {control}
      {c.help && <span className="small muted">{c.help}</span>}
      {c.reason && <span className="small muted r-reason">Why: {c.reason}</span>}
      {(note || disabledNote) && <span id={`${id}-n`} className="small warn-text">{disabledNote ?? note}</span>}
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = {
  identity: "Person or character",
  layout: "Layout or pose",
  garment: "Clothing or object",
  style: "Style",
  other: "Other reference",
};

/** Renders a spec using only the fixed component set. A component whose binding failed validation is not drawn.
 *  Each of the three trusted layouts has its own structure; none of them runs code from the workflow. */
export function Renderer({ spec, verdicts, values, onChange, onRun, runReason, runBusy, onUpload, compact = false }: {
  spec: UISpec; verdicts: ComponentVerdict[]; values: Values; onChange: (id: string, v: Values[string]) => void;
  onRun?: () => void; runReason?: string | null; runBusy?: boolean; onUpload?: (componentId: string, file: File) => Promise<string>;
  compact?: boolean;  // the studio shows its own title and a large result, so the renderer leaves both out
}) {
  const status = new Map(verdicts.map((v) => [v.component, v]));
  // In the studio, people see plain labels; the node and input names are for the builder.
  const visible = spec.components.filter((c) => !c.hidden && status.get(c.id)?.status !== "invalid")
    .map((c) => (compact ? { ...c, help: "", reason: null } : c));   // the studio keeps its controls plain
  const isImage = (c: SpecComponent) => c.type === "image_upload";
  const refs = visible.filter((c) => c.group === "main" && isImage(c));
  const main = visible.filter((c) => c.group === "main" && !isImage(c));
  const adv = visible.filter((c) => c.group === "advanced");
  const draw = (c: SpecComponent) => <Field key={c.id} c={c} verdict={status.get(c.id)} value={values[c.id]} onChange={(v) => onChange(c.id, v)}
    onUpload={onUpload ? (file: File) => onUpload(c.id, file) : undefined} />;
  const drawRef = (c: SpecComponent) => (
    <div key={c.id} className="r-ref">
      {c.role && ROLE_LABEL[c.role] && <span className="pill role-pill">{ROLE_LABEL[c.role]}</span>}
      {draw(c)}
    </div>
  );
  const mainBlock = main.length === 0 ? <p className="muted">No controls are shown in the main panel.</p> : main.map(draw);
  const advBlock = adv.length > 0 && (
    <details className="r-advanced" open={spec.layout === "advanced_workspace"}>
      <summary>Advanced settings ({adv.length})</summary>
      {adv.map(draw)}
    </details>
  );
  const generate = onRun ? (
    <>
      <button type="button" className="r-generate" onClick={onRun} disabled={!!runReason || !!runBusy} aria-describedby="r-run-note">{runBusy ? "Running…" : "Generate"}</button>
      <span id="r-run-note" className="small muted">{runReason ?? "Runs the saved app with these values on your ComfyUI."}</span>
    </>
  ) : (
    <>
      <button type="button" className="r-generate" disabled aria-describedby="r-run-note">Generate</button>
      <span id="r-run-note" className="small muted">This is a preview of the controls.</span>
    </>
  );
  const output = compact ? null : (
    <div className="r-output" role="region" aria-label="Output">
      <h4 className="r-output-h">Output</h4>
      {spec.outputs.length === 0 ? <span className="muted small">This workflow has no output node.</span> : spec.outputs.map((o) => <div key={o.node}><strong>{o.label}</strong><span className="small muted"> · {o.media}</span></div>)}
    </div>
  );

  let body: React.ReactNode;
  if (spec.layout === "reference_guided") {
    body = (
      <div className="r-controls">
        {refs.length > 0 && (
          <fieldset className="r-refs">
            <legend>References</legend>
            <p className="small muted">Add the images this app should follow. Each one is used for the role shown.</p>
            {refs.map(drawRef)}
          </fieldset>
        )}
        {mainBlock}
        {advBlock}
        <div className="r-actions">{generate}</div>
        {output}
      </div>
    );
  } else if (spec.layout === "advanced_workspace") {
    body = (
      <div className="r-workspace">
        <div className="r-settings">
          {refs.length > 0 && <section className="r-group"><h4>References</h4>{refs.map(drawRef)}</section>}
          <section className="r-group"><h4>Main settings</h4>{mainBlock}</section>
          {advBlock}
          <div className="r-actions">{generate}</div>
        </div>
        <div className="r-side">{output}</div>
      </div>
    );
  } else {
    body = (
      <div className="r-controls r-hero">
        {refs.map(drawRef)}
        {mainBlock}
        <div className="r-actions">{generate}</div>
        {advBlock}
        {output}
      </div>
    );
  }
  return (
    <div className={`renderer layout-${spec.layout}`}>
      {!compact && <h3 className="r-title">{spec.name}</h3>}
      {!compact && spec.description && <p className="muted small">{spec.description}</p>}
      {body}
    </div>
  );
}
