import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppBuilder } from "./AppBuilder";
import { Tabs } from "./Tabs";
import { api, forgeApi, type Hardware, forgePackageApi, type ForgeAnalysis, type ForgeDeps, type ForgeDepRow, type ForgeModelFile, type ForgeTemplate, type ForgeWorkflow, type ImportPreview, type NavTab, navTabsApi } from "./api";
import { Chain } from "./Chain";
import { Flow } from "./Flow";
import { NavTabsEditor, tabLabelFor } from "./NavTabs";
import { AppCover, CoverPicker } from "./AppCover";
import { ModelSearch } from "./ModelSearch";
import { GraphView } from "./GraphView";
import { Operator } from "./Operator";
import { SUBMISSION_MODE, withoutFixtures } from "./ui/release";

// A template's mode comes from its ComfyUI group (the category is almost always "Foundation", so it says little).
// Groups not listed here, such as LLM and Node Basics, go under Other.
type TemplateMode = "image" | "video" | "audio" | "3d" | "other";
const TEMPLATE_MODES: { id: TemplateMode; label: string; groups: string[] }[] = [
  { id: "image", label: "Image", groups: ["Image", "Image Tools", "Character & Fashion", "Product & Ads", "Vector", "Brand & Design"] },
  { id: "video", label: "Video", groups: ["Video", "Video Tools"] },
  { id: "audio", label: "Audio", groups: ["Audio"] },
  { id: "3d", label: "3D", groups: ["3D Model"] },
  { id: "other", label: "Other", groups: [] },
];

export const READINESS: Record<ForgeAnalysis["readiness"], { text: string; cls: string }> = {
  ready: { text: "Ready", cls: "good" },
  needs_attention: { text: "Needs attention", cls: "warn" },
  blocked: { text: "Blocked", cls: "bad" },
};

type LibStatus = "all" | "ready" | "needs_attention" | "blocked" | "unchecked";
const LIB_FILTERS: { id: LibStatus; label: string }[] = [
  { id: "all", label: "All" },
  { id: "ready", label: "Ready" },
  { id: "needs_attention", label: "Needs attention" },
  { id: "blocked", label: "Blocked" },
  { id: "unchecked", label: "Unknown" },
];

export const MODEL_STATUS: Record<ForgeModelFile["status"], string> = {
  available: "Installed",
  found_elsewhere: "Installed in another folder",
  ambiguous: "Several matching files",
  missing: "Not installed",
  unknown: "Not checked (ComfyUI not running)",
};

const TONES = ["#2B2019", "#1C2430", "#272030", "#1E2A24", "#2C2422", "#22252C"];

export function toneFor(name: string): string {
  const sum = [...name].reduce((a, c) => a + c.charCodeAt(0), 0);
  return TONES[sum % TONES.length];
}

const DEFS_SOURCE: Record<string, string> = {
  live: "checked against the running ComfyUI",
  cache: "checked against the last saved copy of ComfyUI's node list",
  bundled: "checked against the core node list bundled with Open Workflow",
  none: "no node definitions available",
};

function fmtValue(v: unknown): string {
  if (typeof v === "string") return v.length > 80 ? `${v.slice(0, 80)}…` : v;
  return JSON.stringify(v);
}

function placement(s: { show: boolean; advanced: boolean; hidden: boolean }): string {
  if (s.hidden) return "Hidden";
  if (s.show) return "Main panel";
  if (s.advanced) return "Advanced";
  return "Not shown";
}

export function ImportPanel({ onImported }: { onImported: (w: ForgeWorkflow) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [mode, setMode] = useState<TemplateMode | "all">("all");
  const [templates, setTemplates] = useState<{ available: boolean; reason?: string; templates: ForgeTemplate[] } | null>(null);
  const [q, setQ] = useState("");
  const [openOnly, setOpenOnly] = useState(true);
  const [review, setReview] = useState<{ file: File; preview: ImportPreview } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    forgeApi.templates().then(setTemplates).catch(() => setTemplates({ available: false, reason: "Could not read templates.", templates: [] }));
  }, []);

  /** A workflow file is read first and shown for review; a package is imported directly, since it carries its own checks. */
  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy("file"); setErr(null); setReview(null);
    try {
      if (f.name.toLowerCase().endsWith(".forgeapp")) {
        onImported((await forgePackageApi.importPackage(f)).workflow);
      } else {
        setReview({ file: f, preview: await forgeApi.preview(f) });
      }
    } catch (e) { setErr(e instanceof Error ? e.message : "This file could not be read."); }
    finally { setBusy(null); if (fileRef.current) fileRef.current.value = ""; }
  };

  /** Stores the reviewed file. `copy` stores a second copy of a file that is already imported. */
  const confirmImport = async (copy: boolean) => {
    if (!review) return;
    setBusy("file"); setErr(null);
    try {
      const r = await forgeApi.upload(review.file, copy);
      setReview(null);
      onImported(r.workflow);
    } catch (e) { setErr(e instanceof Error ? e.message : "Import failed."); }
    finally { setBusy(null); }
  };

  const fromTemplate = async (name: string) => {
    setBusy(name); setErr(null);
    try { onImported((await forgeApi.importTemplate(name)).workflow); } catch (e) { setErr(e instanceof Error ? e.message : "Import failed."); }
    finally { setBusy(null); }
  };

  const modeOf = (x: ForgeTemplate): TemplateMode => TEMPLATE_MODES.find((m) => m.groups.includes(x.group ?? ""))?.id ?? "other";
  const modeCounts = useMemo(() => {
    const counts: Record<string, number> = { all: 0 };
    for (const x of templates?.templates ?? []) {
      if (openOnly && !(x.open_source && !x.uses_paid_api)) continue;
      counts.all += 1;
      counts[modeOf(x)] = (counts[modeOf(x)] ?? 0) + 1;
    }
    return counts;
  }, [templates, openOnly]);
  const shown = useMemo(() => {
    const t = (templates?.templates ?? []).filter((x) => (!openOnly || (x.open_source && !x.uses_paid_api)) && (mode === "all" || modeOf(x) === mode));
    const s = q.trim().toLowerCase();
    return (s ? t.filter((x) => `${x.title} ${x.name} ${x.models.join(" ")} ${x.group}`.toLowerCase().includes(s)) : t).slice(0, 60);
  }, [templates, q, openOnly, mode]);

  return (
    <div className="import-body">
      <div
        className={`dropzone${drag ? " over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); void upload(e.dataTransfer.files[0]); }}
      >
        <p className="dz-title">Drop a workflow or app package here</p>
        <p className="small muted">A ComfyUI workflow (.json) or a Forge app package (.forgeapp). Nothing runs during import, and no model files are included or needed to import.</p>
        <label className="file-btn">
          <input ref={fileRef} type="file" accept=".json,.forgeapp,application/json,application/zip" onChange={(e) => void upload(e.target.files?.[0])} disabled={busy !== null} />
          <span>{busy === "file" ? "Importing…" : "Choose a file"}</span>
        </label>
      </div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {review && (
        <section className="import-review" aria-labelledby="ir-h">
          <h3 id="ir-h" className="sub-h">Review before importing</h3>
          <dl className="import-facts">
            <dt>File</dt><dd>{review.preview.name}</dd>
            <dt>Format</dt><dd>{review.preview.format === "ui" ? "Editor workflow (has a layout)" : "API prompt (runs, no layout)"}</dd>
            <dt>Nodes</dt><dd>{review.preview.nodes}{review.preview.subgraphs ? ` · ${review.preview.subgraphs} subgraph${review.preview.subgraphs > 1 ? "s" : ""}` : ""}</dd>
            <dt>Size</dt><dd>{(review.preview.bytes / 1024).toFixed(1)} KB</dd>
          </dl>
          {review.preview.unknown_count > 0 && (
            <p className="warn-text small">
              ComfyUI does not know {review.preview.unknown_count} node type{review.preview.unknown_count > 1 ? "s" : ""}, so
              {" "}{review.preview.unknown_node_types.slice(0, 6).join(", ")}{review.preview.unknown_count > 6 ? " …" : ""} may need a custom node pack.
            </p>
          )}
          {review.preview.already_imported.length > 0 ? (
            <div className="banner">
              This file is already imported as <strong>{review.preview.already_imported[0].name}</strong>.
              <div className="review-actions">
                <button type="button" disabled={busy !== null} onClick={() => void confirmImport(false)}>Open the existing app</button>
                <button type="button" className="secondary" disabled={busy !== null} onClick={() => void confirmImport(true)}>Import a copy</button>
                <button type="button" className="secondary" onClick={() => setReview(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="review-actions">
              <button type="button" disabled={busy !== null} onClick={() => void confirmImport(false)}>{busy === "file" ? "Importing…" : "Import this file"}</button>
              <button type="button" className="secondary" onClick={() => setReview(null)}>Cancel</button>
            </div>
          )}
        </section>
      )}
      <h3 className="sub-h">Or start from a ComfyUI template</h3>
      {templates === null && <div className="skeleton" aria-busy="true" />}
      {templates && !templates.available && <p className="muted small">{templates.reason}</p>}
      {templates?.available && (
        <>
          <Tabs<TemplateMode | "all">
            label="Template mode"
            value={mode}
            onChange={setMode}
            items={[{ id: "all", label: `All ${modeCounts.all ?? 0}` }, ...TEMPLATE_MODES.map((m) => ({ id: m.id, label: `${m.label} ${modeCounts[m.id] ?? 0}` }))]}
          />
          <div className="toolbar">
            <input type="search" aria-label="Search templates" placeholder="Search by name or model" value={q} onChange={(e) => setQ(e.target.value)} />
            <label className="check"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Open local models only</label>
          </div>
          {shown.length === 0 && <p className="muted small">No templates match.</p>}
          <ul className="tpl-grid" aria-label="Templates">
            {shown.map((t) => (
              <li key={t.name} className="tpl-card">
                <div className="tpl-thumb" aria-hidden="true">
                  <span className="tpl-tag">{t.group ?? t.category ?? "Template"}</span>
                </div>
                <div className="tpl-meta">
                  {t.uses_paid_api && <span className="pill warn">Paid online API</span>}
                </div>
                <strong className="tpl-title">{t.title}</strong>
                <span className="small muted tpl-models">{t.models.join(", ") || "No models listed"}</span>
                <button disabled={busy !== null} onClick={() => void fromTemplate(t.name)} aria-label={`Use template ${t.title}`}>
                  {busy === t.name ? "Importing…" : "Use template"}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,button,select")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" ref={ref}>
        <div className="modal-head">
          <h2 id="modal-title">{title}</h2>
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  );
}

const DEP_STATUS: Record<ForgeDepRow["status"], { text: string; cls: string }> = {
  registered: { text: "Registered", cls: "good" },
  core: { text: "Core ComfyUI", cls: "good" },
  unchecked: { text: "Not checked", cls: "muted" },
  missing: { text: "Not registered, no pack found", cls: "bad" },
  candidate: { text: "Likely pack", cls: "warn" },
  choose: { text: "Several packs: choose", cls: "warn" },
};

function DepsSection({ d }: { d: ForgeDeps }) {
  const [open, setOpen] = useState(false);
  const rows = d.requirements.filter((r) => r.status !== "core" && r.status !== "registered");
  const shown = open ? rows : rows.slice(0, 8);
  return (
    <section className="deps" aria-labelledby="deps-h">
      <h3 id="deps-h" className="sub-h">Custom nodes</h3>
      <p className={d.counts.missing || d.counts.choose || d.counts.candidate ? "warn-text" : "ok"}>{d.headline}</p>
      <p className="small muted">
        {d.node_classes_needed} node classes needed ({d.node_instances_needed} nodes).
        {" "}Candidate packs come from ComfyUI Manager's mapping ({d.pack_index}). A candidate is not installed. Nothing is installed by Forge yet.
      </p>
      {rows.length === 0 ? <p className="muted small">No custom nodes are needed beyond ComfyUI's core.</p> : (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Node class</th><th scope="col">Status</th><th scope="col">Candidate packs</th><th scope="col">Used by</th></tr></thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.class}>
                    <td><code>{r.class}</code>{r.titles.length > 0 && <span className="small muted"> {r.titles[0]}</span>}</td>
                    <td><span className={`pill ${DEP_STATUS[r.status].cls}`}>{DEP_STATUS[r.status].text}</span></td>
                    <td>
                      {r.candidates.length === 0 && <span className="muted small">none in Manager's list</span>}
                      {r.candidates.map((c) => (
                        <div key={c.repository} className="small">
                          <a href={c.repository} target="_blank" rel="noopener noreferrer">{c.title ?? c.repository.split("/").pop()}</a>
                          <span className="muted"> · {c.repository.replace(/^https?:\/\//, "")}</span>
                        </div>
                      ))}
                    </td>
                    <td className="small muted">{r.nodes.length} node{r.nodes.length === 1 ? "" : "s"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 8 && <button className="link" onClick={() => setOpen(!open)}>{open ? "Show fewer" : `Show all ${rows.length}`}</button>}
        </>
      )}
      <ul className="small muted">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </section>
  );
}

function Analysis({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [data, setData] = useState<{ workflow: ForgeWorkflow; analysis: ForgeAnalysis } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState("");
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(() => {
    setData(null); setErr(null);
    forgeApi.analysis(id).then(setData).catch((e) => setErr(e instanceof Error ? e.message : "Analysis failed."));
  }, [id]);
  useEffect(load, [load]);

  if (err) return <section className="panel"><div className="banner bad" role="alert">{err}</div><button className="secondary" onClick={load}>Try again</button></section>;
  if (!data) return <section className="panel" aria-busy="true"><div className="skeleton" style={{ height: 220 }} /></section>;
  const { workflow: w, analysis: a } = data;
  const r = READINESS[a.readiness];
  const controls = showAll ? a.controls : a.controls.filter((c) => !c.suggested.hidden);
  const counts = { error: 0, warning: 0, info: 0 };
  a.problems.forEach((p) => { counts[p.severity] += 1; });

  return (
    <section className="panel analysis">
      <div className="pills">
        <span className={`pill ${r.cls}`} role="status">{r.text}</span>
        <span className="pill info" title={`Inferred from ${a.task.rule}`}>{a.task.family.replace(/-/g, " ")} (inferred)</span>
        {a.app_builder && <span className="pill muted">Has ComfyUI App Builder selections</span>}
      </div>
      {a.reasons.length > 0 && <ul className="reasons">{a.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
      <p className="small muted">Node definitions: {DEFS_SOURCE[a.node_definitions.source]}{a.node_definitions.comfyui_version ? ` (ComfyUI ${a.node_definitions.comfyui_version})` : ""}. {a.node_definitions.note}</p>
      <dl className="stat-row">
        <div><dt>Nodes</dt><dd>{a.counts.nodes}</dd></div>
        <div><dt>Controls</dt><dd>{a.counts.controls}</dd></div>
        <div><dt>Outputs</dt><dd>{a.counts.outputs}</dd></div>
        <div><dt>Model files</dt><dd>{a.counts.model_files}</dd></div>
        <div><dt>Unknown nodes</dt><dd>{a.counts.unknown_node_types}</dd></div>
      </dl>

      <h3 className="sub-h">Controls</h3>
      <p className="small muted">Type, value and limits come from the file and ComfyUI's node definitions. The role and placement are Forge's suggestions.</p>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th scope="col">Node</th><th scope="col">Input</th><th scope="col">Role</th><th scope="col">Type</th><th scope="col">Value</th><th scope="col">Placement</th></tr></thead>
          <tbody>
            {controls.map((c) => (
              <tr key={c.id}>
                <td>{c.node_title ?? c.class_type} <span className="muted small">#{c.node}</span></td>
                <td><code>{c.input}</code></td>
                <td title={`Inferred: ${c.inferred.rule}`}>{c.inferred.kind.replace(/_/g, " ")}{c.app_builder_selected && <span className="cap cap-honoured">App Builder</span>}</td>
                <td>{c.fact.type ?? "?"}</td>
                <td className="val">{fmtValue(c.fact.value)}</td>
                <td>{placement(c.suggested)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="link" onClick={() => setShowAll(!showAll)}>{showAll ? "Hide hidden inputs" : "Show hidden inputs too"}</button>

      <h3 className="sub-h">Outputs</h3>
      {a.outputs.length === 0 ? <p className="warn-text">No output node found.</p> : (
        <ul className="plain">{a.outputs.map((o) => <li key={o.node}>{o.title ?? o.class_type} <span className="muted small">#{o.node} · {o.media}</span>{o.app_builder_selected && <span className="cap cap-honoured">App Builder</span>}</li>)}</ul>
      )}

      <DepsSection d={a.dependencies} />

      <h3 className="sub-h">Model files</h3>
      {a.model_files.length === 0 ? <p className="muted">No model files referenced.</p> : (
        <ul className="plain">
          {a.model_files.map((m) => (
            <li key={m.id}>
              <code>{m.file}</code> — <span className={m.status === "available" || m.status === "found_elsewhere" ? "ok" : m.status === "unknown" ? "muted" : "warn-text"}>{MODEL_STATUS[m.status]}</span>
              {m.status === "found_elsewhere" && m.comfy_name && <span className="small muted"> as <code>{m.comfy_name}</code></span>}
              {m.alternatives.length > 0 && <span className="small muted"> ({m.alternatives.join(", ")})</span>}
            </li>
          ))}
        </ul>
      )}

      {a.unknown_node_types.length > 0 && (
        <>
          <h3 className="sub-h">Node types ComfyUI does not have</h3>
          <p className="small">{a.unknown_node_types.join(", ")}</p>
          {a.custom_node_packs.length > 0 && <p className="small muted">The file names these packs: {a.custom_node_packs.map((p) => `${p.id}${p.version ? ` ${p.version}` : ""}`).join(", ")}. Forge does not install custom nodes.</p>}
        </>
      )}

      <h3 className="sub-h">Problems ({counts.error} errors, {counts.warning} warnings, {counts.info} notes)</h3>
      {a.problems.length === 0 ? <p className="ok">None found.</p> : (
        <ul className="warnings">
          {a.problems.map((p, i) => <li key={i} className={p.severity === "error" ? "w-error" : p.severity === "warning" ? "w-warn" : "w-info"}><strong>{p.severity === "error" ? "Error" : p.severity === "warning" ? "Check" : "Note"}:</strong> {p.message}</li>)}
        </ul>
      )}

      <div className="card danger">
        <h3 className="sub-h">Remove from Forge</h3>
        <p className="small muted">Removes Forge's copy of this workflow and its app. ComfyUI and your model files are not touched. Type the name to confirm.</p>
        <div className="inline-form">
          <label>Name<input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
          <button className="danger-btn" disabled={confirm !== w.name} onClick={() => void forgeApi.remove(w.id, confirm).then(onChanged).catch(() => undefined)}>Remove</button>
        </div>
      </div>
    </section>
  );
}

/**
 * Compares the weights an app loads with the largest GPU's VRAM. The weights are a floor: a run also needs
 * working memory, so "fits" means the files alone fit, not that the run will succeed. Null when there is nothing to say.
 */
export function gpuFit(weights: ForgeAnalysis["weights"] | undefined, gpu: Hardware["gpus"][number] | null): { text: string; tone: "good" | "warn" | "bad" | "muted" } | null {
  if (!weights || (weights.measured === 0 && weights.unmeasured === 0)) return null;
  if (weights.measured === 0) return { text: "Run a model scan", tone: "muted" };
  const need = (weights.total_bytes / 1024 ** 3).toFixed(1);
  if (!gpu) return { text: `Needs ${need} GB of files`, tone: "muted" };
  const have = (gpu.vram_total_mb / 1024).toFixed(0);
  const ratio = weights.total_bytes / (gpu.vram_total_mb * 1024 * 1024);
  if (ratio > 1) return { text: `Files need ${need} GB; ${gpu.name} has ${have} GB`, tone: "bad" };
  if (ratio > 0.8) return { text: `Files take ${need} GB of ${have} GB; tight`, tone: "warn" };
  return { text: `Files take ${need} GB of ${have} GB`, tone: "good" };
}

/**
 * "Can I run this?" in one place. States what is ready, what is missing, and the single next step.
 * Facts come from the same analysis the Analysis tab shows; nothing here is a separate claim.
 */
function ReadinessCard({ id, onOpenDetail, onBuild }: { id: string; onOpenDetail: () => void; onBuild: () => void }) {
  const [data, setData] = useState<ForgeAnalysis | null>(null);
  const [failed, setFailed] = useState(false);
  const [gpu, setGpu] = useState<Hardware["gpus"][number] | null>(null);
  useEffect(() => {
    let alive = true;
    setData(null); setFailed(false);
    forgeApi.analysis(id).then((r) => { if (alive) setData(r.analysis); }).catch(() => { if (alive) setFailed(true); });
    api.hardware().then((h) => { if (alive) setGpu(h.gpus.reduce<Hardware["gpus"][number] | null>((a, g) => (!a || g.vram_total_mb > a.vram_total_mb ? g : a), null)); }).catch(() => undefined);
    return () => { alive = false; };
  }, [id]);
  if (failed) return <div className="ready-card muted small">Could not check this app right now.</div>;
  if (!data) return <div className="ready-card skeleton" aria-busy="true" style={{ height: 84 }} />;

  const modelsMissing = data.model_files.filter((m) => m.status === "missing" || m.status === "ambiguous").length;
  const modelsUnchecked = data.model_files.filter((m) => m.status === "unknown").length;
  const nodesMissing = data.dependencies.counts.missing + data.dependencies.counts.choose;
  const nodesUnchecked = data.dependencies.counts.unchecked;
  const controlsBad = data.counts.controls === 0;
  const memory = gpuFit(data.weights, gpu);

  type Step = { label: string; value: string; tone: "good" | "warn" | "bad" | "muted" };
  const steps: Step[] = [
    { label: "Controls", value: controlsBad ? "None found" : `${data.counts.controls} ready to use`, tone: controlsBad ? "bad" : "good" },
    { label: "Model files", value: modelsMissing ? `${modelsMissing} missing` : modelsUnchecked ? "Not checked yet" : `${data.counts.model_files} installed`, tone: modelsMissing ? "bad" : modelsUnchecked ? "muted" : "good" },
    { label: "Custom nodes", value: nodesMissing ? `${nodesMissing} to resolve` : nodesUnchecked ? "Start ComfyUI to check" : "All registered", tone: nodesMissing ? "warn" : nodesUnchecked ? "muted" : "good" },
    ...(memory ? [{ label: "GPU memory", value: memory.text, tone: memory.tone }] : []),
  ];

  const blocked = data.readiness === "blocked";
  // "Almost ready" only when something is actually left to do; analysis notes alone do not block a run.
  const actionable = controlsBad || modelsMissing > 0 || nodesMissing > 0;
  const state = blocked ? "blocked" : actionable ? "attention" : "ready";
  const next = blocked
    ? { text: data.reasons[0] ?? "Fix the blocking issue to continue.", action: "See why", go: onOpenDetail }
    : actionable
      ? { text: "Resolve the item above to run this app. You can keep designing it in the meantime.", action: "Open the builder", go: onBuild }
      : { text: data.readiness === "ready" ? "Everything this app needs is in place. Set your values and run it." : "Some model files are installed in another folder. Forge runs with those files. Details lists them.", action: "Open the builder", go: onBuild };

  return (
    <section className={`ready-card is-${state}`} aria-labelledby="ready-h">
      <div className="ready-head">
        <h3 id="ready-h" className="ready-title">{state === "blocked" ? "Blocked for now" : state === "ready" ? (data.readiness === "ready" ? "Ready to run" : "Ready, with notes") : "Almost ready"}</h3>
        <button className="ghost" onClick={onOpenDetail}>Details</button>
      </div>
      <ul className="ready-steps" aria-label="What this app needs">
        {steps.map((st) => (
          <li key={st.label} className={`ready-step tone-${st.tone}`}>
            <span className="ready-step-label">{st.label}</span>
            <span className="ready-step-value">{st.value}</span>
          </li>
        ))}
      </ul>
      <div className="ready-next">
        <p className="small">{next.text}</p>
        <button className="secondary" onClick={next.go}>{next.action}</button>
      </div>
    </section>
  );
}

export function Apps({ openId, openTab = "use", onDetails, onOpened, onOpenChange, tabs = [], onTabsChanged }: { openId?: string | null; openTab?: "use" | "build" | "analysis"; onDetails?: (id: string) => void; onOpened?: () => void; onOpenChange?: (id: string | null) => void; tabs?: NavTab[]; onTabsChanged?: (tabs: NavTab[]) => void }) {
  const [list, setList] = useState<ForgeWorkflow[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"use" | "build" | "analysis">("use");
  const [open, setOpen] = useState<string | null>(null);  // the app shown full page; null shows the library
  const [importing, setImporting] = useState(false);
  const [browse, setBrowse] = useState(false);
  const [libQuery, setLibQuery] = useState("");
  const [libSort, setLibSort] = useState<"newest" | "name">("newest");
  const [libStatus, setLibStatus] = useState<LibStatus>("all");
  const [libView, setLibView] = useState<"grid" | "list">("grid");
  const [chaining, setChaining] = useState(false);
  const [flowing, setFlowing] = useState(false);
  const [operating, setOperating] = useState(false);
  const [navEditing, setNavEditing] = useState(false);
  const [pinErr, setPinErr] = useState<string | null>(null);

  // Tell the shell which app is open, so its nav tab can show as current.
  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);

  // A nav tab asks for one app: show its full page.
  useEffect(() => {
    if (!openId) return;
    setOpen(openId);
    setTab(openTab);
    onOpened?.();
    // Only a new request should reopen an app; onOpened changes identity on every render of the parent.
  }, [openId]);

  const pinned = (id: string) => tabs.some((t) => t.app_id === id);
  const togglePin = async (w: ForgeWorkflow) => {
    setPinErr(null);
    const next = pinned(w.id)
      ? tabs.filter((t) => t.app_id !== w.id)
      : [...tabs, { app_id: w.id, label: tabLabelFor(w) }];
    try {
      const r = await navTabsApi.save(next);
      onTabsChanged?.(r.tabs);
    } catch (e) { setPinErr(e instanceof Error ? e.message : "Could not change the nav tabs."); }
  };

  const load = useCallback(async (select?: string | null) => {
    try {
      const l = withoutFixtures((await forgeApi.list()).workflows);
      setList(l);
      setSel((cur) => (select !== undefined ? select : cur && l.some((w) => w.id === cur) ? cur : l[0]?.id ?? null));
    } catch (e) { setErr(e instanceof Error ? e.message : "Could not load workflows."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const close = useCallback(() => setImporting(false), []);
  const [pkgErr, setPkgErr] = useState<string | null>(null);

  const exportApp = async (w: ForgeWorkflow) => {
    setPkgErr(null);
    try {
      const blob = await forgePackageApi.exportBlob(w.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `${w.name.replace(/[^A-Za-z0-9_.-]+/g, "_").slice(0, 60) || "app"}.forgeapp`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setPkgErr(e instanceof Error ? e.message : "Export failed."); }
  };

  const duplicate = async (w: ForgeWorkflow) => {
    setPkgErr(null);
    try { const r = await forgePackageApi.duplicate(w.id); await load(r.workflow.id); setOpen(r.workflow.id); setTab("use"); }
    catch (e) { setPkgErr(e instanceof Error ? e.message : "Could not duplicate."); }
  };
  const onImported = (w: ForgeWorkflow) => { setImporting(false); setBrowse(false); setTab("use"); setOpen(w.id); void load(w.id); };
  const current = list?.find((w) => w.id === open) ?? null;
  const libCounts = useMemo(() => {
    const c: Record<LibStatus, number> = { all: 0, ready: 0, needs_attention: 0, blocked: 0, unchecked: 0 };
    for (const w of list ?? []) {
      c.all += 1;
      const key: LibStatus = w.readiness ? w.readiness : "unchecked";
      c[key] += 1;
    }
    return c;
  }, [list]);
  const visibleApps = useMemo(() => {
    const q = libQuery.trim().toLowerCase();
    const rows = (list ?? []).filter((w) => {
      if (q && !w.name.toLowerCase().includes(q)) return false;
      if (libStatus === "all") return true;
      return libStatus === "unchecked" ? !w.readiness : w.readiness === libStatus;
    });
    return rows.sort((a, b) => (libSort === "name" ? a.name.localeCompare(b.name) : b.imported_at.localeCompare(a.imported_at)));
  }, [list, libQuery, libSort, libStatus]);

  return (
    <div className="page wide apps">
      <header className="page-head">
        <div>
          <p className="eyebrow">Discover</p>
          <h1>Apps</h1>
          <p className="lede">Each app is a ComfyUI workflow with its controls turned into a simple form. Open Workflow checks what it needs before you run it.</p>
        </div>
        <div className="actions">
          {!SUBMISSION_MODE && (<>
          <button className="secondary" onClick={() => setOperating((o) => !o)} aria-expanded={operating}>{operating ? "Close OW agent" : "OW agent"}</button>
          <button className="secondary" onClick={() => setChaining((c) => !c)} aria-expanded={chaining}>{chaining ? "Close chain" : "Chain two apps"}</button>
          <button className="secondary" onClick={() => setFlowing((f) => !f)} aria-expanded={flowing}>{flowing ? "Close flow" : "Run a flow"}</button>
          <button className="secondary" onClick={() => setNavEditing((n) => !n)} aria-expanded={navEditing}>{navEditing ? "Close nav tabs" : "Nav tabs"}</button>
          </>)}
          <button onClick={() => setImporting(true)}>Import workflow</button>
        </div>
      </header>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {pkgErr && <div className="banner bad" role="alert">{pkgErr}</div>}

      {list === null && <div className="skeleton" aria-busy="true" style={{ height: 180 }} />}

      {operating && <Operator />}
      {chaining && list && <Chain apps={list} onClose={() => setChaining(false)} />}
      {flowing && list && <Flow apps={list} onClose={() => setFlowing(false)} />}
      {navEditing && list && <NavTabsEditor tabs={tabs} apps={list} onSaved={(t) => onTabsChanged?.(t)} />}
      {pinErr && <div className="banner bad" role="alert">{pinErr}</div>}

      {list && list.length === 0 && (
        <section className="empty-hero" aria-labelledby="start-h">
          <h2 id="start-h">Start your first app</h2>
          <p className="muted">Bring a workflow you already have, or start from one of ComfyUI's templates. Forge shows what each one needs before anything runs.</p>
          <ImportPanel onImported={onImported} />
        </section>
      )}

      {list && list.length > 0 && (
        <>
          {!current && (<>
          <div className="actions" style={{ marginBottom: 8 }}>
            <button className="secondary" aria-expanded={browse} onClick={() => setBrowse((b) => !b)}>
              {browse ? "Hide templates" : "Browse ComfyUI templates"}
            </button>
          </div>
          {browse && (
            <section className="card" aria-label="Templates and import">
              <ImportPanel onImported={onImported} />
            </section>
          )}
          <div className="lib-filters" role="group" aria-label="Filter apps by status">
            {LIB_FILTERS.map((f) => (
              <button key={f.id} type="button" className={`lib-chip${libStatus === f.id ? " on" : ""}`} aria-pressed={libStatus === f.id}
                onClick={() => setLibStatus(f.id)} disabled={libCounts[f.id] === 0 && f.id !== "all"}>
                {f.label} <span className="lib-count">{libCounts[f.id]}</span>
              </button>
            ))}
          </div>
          <div className="lib-tools">
            <input type="search" aria-label="Search your apps" placeholder="Search apps by name" value={libQuery} onChange={(e) => setLibQuery(e.target.value)} />
            <select aria-label="Sort apps" value={libSort} onChange={(e) => setLibSort(e.target.value as "newest" | "name")}>
              <option value="newest">Newest first</option>
              <option value="name">Name, A to Z</option>
            </select>
            <div className="lib-view" role="group" aria-label="Library layout">
              <button type="button" className={libView === "grid" ? "on" : ""} aria-pressed={libView === "grid"} onClick={() => setLibView("grid")}>Grid</button>
              <button type="button" className={libView === "list" ? "on" : ""} aria-pressed={libView === "list"} onClick={() => setLibView("list")}>List</button>
            </div>
          </div>
          {visibleApps.length === 0 && (
            <div className="lib-empty" role="status">
              <p className="muted">No apps match this filter{libQuery ? ` and the name "${libQuery}"` : ""}.</p>
              <button type="button" className="secondary" onClick={() => { setLibStatus("all"); setLibQuery(""); }}>Show all apps</button>
            </div>
          )}
          <nav className={`lib-grid${libView === "list" ? " lib-list" : ""}`} aria-label="Your apps">
            {visibleApps.map((w) => (
              <button key={w.id} className={`lib-card${w.id === sel ? " selected" : ""}`} aria-current={w.id === sel ? "true" : undefined}
                onClick={() => { setSel(w.id); setOpen(w.id); setTab("use"); }}>
                <AppCover id={w.id} tone={toneFor(w.name)} name={w.name} tag={w.origin.startsWith("comfyui-template:") ? "Template" : "Uploaded"} />
                <span className="lib-body">
                  <span className="lib-name">{w.name}</span>
                  <span className="small muted">{w.format === "ui" ? "Editor workflow" : "API prompt"} · {new Date(w.imported_at).toLocaleDateString()}</span>
                  {w.readiness && <span className={`pill ${READINESS[w.readiness].cls}`}>{READINESS[w.readiness].text}</span>}
                  <span className="lib-open">{w.readiness === "ready" ? "Run" : w.readiness ? "See what's missing" : "Open"} →</span>
                </span>
              </button>
            ))}
          </nav>
          </>)}

          {current && (
            <section className="workspace app-view" aria-labelledby="ws-title">
              <button type="button" className="link back-link" onClick={() => setOpen(null)}>← All apps</button>
              {tab !== "use" && <ReadinessCard id={current.id} onOpenDetail={() => setTab("analysis")} onBuild={() => setTab("build")} />}
              <div className="ws-head">
                <div className="min0">
                  <h2 id="ws-title" className="ws-title">{current.name}</h2>
                  <span className="small muted">{current.format === "ui" ? "Editor workflow" : "API prompt"} · {(current.size / 1024).toFixed(0)} KB</span>
                </div>
                <div className="ws-actions">
                  {onDetails && <button className="secondary" onClick={() => onDetails(current.id)}>App details</button>}
                  <CoverPicker id={current.id} compact onChanged={() => void load(current.id)} />
                  <button className="secondary" onClick={() => void togglePin(current)} aria-pressed={pinned(current.id)}>{pinned(current.id) ? "Unpin from nav" : "Pin to nav"}</button>
                  <button className="secondary" onClick={() => void duplicate(current)}>Duplicate</button>
                  <button className="secondary" onClick={() => void exportApp(current)}>Export .forgeapp</button>
                </div>
                <Tabs
                  label="Workspace views"
                  value={tab}
                  onChange={setTab}
                  items={[
                    { id: "use", label: "Use", panelId: "panel-main" },
                    { id: "build", label: "Edit app", panelId: "panel-main" },
                    { id: "analysis", label: "Analysis", panelId: "panel-main" },
                  ]}
                />
              </div>
              <div id="panel-main" role="tabpanel" aria-labelledby={`tab-${tab}`}>
                {tab === "use" && <AppBuilder key={`${current.id}-use`} workflowId={current.id} mode="use" onEdit={() => setTab("build")} />}
                {tab === "build" && <AppBuilder key={`${current.id}-edit`} workflowId={current.id} mode="edit" />}
                {tab === "analysis" && <Analysis key={current.id} id={current.id} onChanged={() => void load(null)} />}
                {tab === "analysis" && <ModelSearch workflowId={current.id} />}
                {tab === "analysis" && <GraphView workflowId={current.id} />}
              </div>
            </section>
          )}
        </>
      )}

      {importing && (
        <Modal title="Import a workflow" onClose={close}>
          <ImportPanel onImported={onImported} />
        </Modal>
      )}
    </div>
  );
}
