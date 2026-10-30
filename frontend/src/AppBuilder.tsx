import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { forgeApi, forgeModelApi, forgeUploadApi, jobsApi, type LlmStatus, type ProposalResult, type SpecValidation, type SpecView, type UISpec } from "./api";
import { RunReceipt } from "./RunReceipt";
import { Renderer, defaultValues, type Values } from "./Renderer";
import { forgeRunsApi, type RunCompare } from "./api";
import { coverUrl } from "./AppCover";
import { LoraStack } from "./LoraStack";

const VALIDATION: Record<string, { text: string; cls: string }> = {
  valid: { text: "All bindings verified", cls: "good" },
  needs_attention: { text: "Some bindings cannot be checked yet", cls: "warn" },
  invalid: { text: "Invalid bindings", cls: "bad" },
};
const LAYOUT_TEXT: Record<string, string> = { simple_generator: "Simple generator", reference_guided: "Reference-guided", advanced_workspace: "Advanced workspace" };

const TERMINAL_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"]);
const RUN_TEXT: Record<string, string> = {
  QUEUED: "Queued", VALIDATING: "Checking the ComfyUI connection", RUNNING: "Running on ComfyUI", POSTPROCESSING: "Saving the output",
  CANCELLING: "Cancelling…", COMPLETED: "Finished", FAILED: "Failed", CANCELLED: "Cancelled", INTERRUPTED: "Interrupted", RECOVERY_REQUIRED: "Output can be recovered",
};

type RunState = { id: string; state: string; error: string | null; notes: string[]; hasOutput: boolean; provider: string };

/** One app. "use" is the studio: controls, a large result and the run gallery. "edit" is the builder. */
export function AppBuilder({ workflowId, mode = "edit", onEdit }: { workflowId: string; mode?: "use" | "edit"; onEdit?: () => void }) {
  const [view, setView] = useState<SpecView | null>(null);
  const [spec, setSpec] = useState<UISpec | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [values, setValues] = useState<Values>({});
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [validation, setValidation] = useState<SpecView["validation"] | null>(null);
  const [runJob, setRunJob] = useState<RunState | null>(null);
  const [showReceipt, setShowReceipt] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [expanded, setExpanded] = useState<{ src: string; label: string } | null>(null);

  const adopt = useCallback((v: SpecView, markSaved: boolean) => {
    setView(v); setSpec(v.spec); setValidation(v.validation); setValues(defaultValues(v.spec));
    if (markSaved) setSaved(JSON.stringify(v.spec));
  }, []);

  const load = useCallback(() => {
    setView(null); setErr(null); setNote(null);
    forgeApi.spec(workflowId).then((v) => adopt(v, v.source === "saved")).catch((e) => setErr(e instanceof Error ? e.message : "Could not load the app."));
  }, [workflowId, adopt]);
  useEffect(load, [load]);

  const dirty = spec !== null && JSON.stringify(spec) !== saved;
  const verdicts = validation?.components ?? [];
  const verdictOf = useMemo(() => new Map(verdicts.map((v) => [v.component, v])), [verdicts]);

  const edit = (fn: (s: UISpec) => UISpec) => {
    if (!spec) return;
    const next = fn(structuredClone(spec));
    setSpec(next); setNote(null);
    void forgeApi.validateSpec(workflowId, next).then((r) => setValidation(r.validation)).catch((e) => setErr(e.message));
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null); setNote(null);
    try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : "Action failed."); } finally { setBusy(false); }
  };

  // Apply a past run's values. Only controls this app has are set, and only plain values, so a receipt cannot inject anything.
  const reuse = (received: Record<string, unknown>) => {
    if (!spec) return;
    const ids = new Set(spec.components.map((c) => c.id));
    const applied: string[] = [];
    setValues((cur) => {
      const next: Values = { ...cur };
      for (const [k, v] of Object.entries(received)) {
        if (ids.has(k) && (typeof v === "string" || typeof v === "number" || typeof v === "boolean")) { next[k] = v; applied.push(k); }
      }
      return next;
    });
    setNote(applied.length ? "Values from that run are applied. Review them, then press Generate." : "That run has no values this app can use.");
  };

  // Poll the run until it finishes. The worker is the only writer, so polling is read-only.
  useEffect(() => {
    if (!runJob || TERMINAL_STATES.has(runJob.state)) return;
    const t = window.setInterval(() => {
      forgeApi.runStatus(runJob.id).then((s) => setRunJob({ id: s.id, state: s.state, error: s.error, notes: s.notes, hasOutput: !!s.output_file, provider: s.provider }))
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(t);
  }, [runJob?.id, runJob?.state]);

  const [provider, setProvider] = useState<"local" | "cloud">("local");
  const [providerState, setProviderState] = useState<{ local: { state: string; message: string }; cloud: { state: string; message: string } } | null>(null);
  const [consentPreview, setConsentPreview] = useState<Awaited<ReturnType<typeof forgeApi.cloudPreview>>["preview"] | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);

  // Refreshed, not read once: ComfyUI may start or stop while the page is open.
  useEffect(() => {
    const load = () => forgeApi.providers().then(setProviderState).catch(() => setProviderState(null));
    load();
    const t = window.setInterval(load, 10000);
    return () => window.clearInterval(t);
  }, []);

  const startRun = async (consent?: { acknowledged: boolean; digest: string }) => {
    setRunErr(null); setStarting(true);
    try {
      if (provider === "cloud" && !consent) {
        // Cloud is never started from one click: show exactly what would be sent first.
        const pv = await forgeApi.cloudPreview(workflowId, values);
        if (pv.status.state !== "ready") throw new Error(pv.status.message);
        setConsentPreview(pv.preview); setConsentChecked(false);
        return;
      }
      const r = await forgeApi.run(workflowId, values, provider, consent);
      setConsentPreview(null);
      setRunJob({ id: r.job.id, state: r.job.state, error: null, notes: r.job.notes, hasOutput: false, provider: r.job.provider });
    } catch (e) {
      setRunErr(e instanceof Error ? e.message : "Could not start the run.");
    } finally { setStarting(false); }
  };

  const cancelRun = async () => {
    if (!runJob) return;
    try { await jobsApi.cancel(runJob.id); } catch (e) { setRunErr(e instanceof Error ? e.message : "Could not cancel."); }
  };

  if (err && !view) return <div><div className="banner bad" role="alert">{err}</div><button className="secondary" onClick={load}>Try again</button></div>;
  if (!view || !spec) return <div className="skeleton" aria-busy="true" style={{ height: 240 }} />;

  const v = VALIDATION[validation?.status ?? view.validation.status];
  const runReason: string | null = dirty ? "Save your changes first. A run uses the saved app."
    : view.readiness === "blocked" ? `The workflow is blocked: ${view.reasons[0] ?? "see the analysis"}`
    : (validation?.status ?? view.validation.status) !== "valid" ? "Every control must be verified before the app can run. See the Controls list."
    : null;
  const active = runJob !== null && !TERMINAL_STATES.has(runJob.state);
  const onUpload = async (id: string, file: File) => {
    setErr(null);
    try {
      const u = await forgeUploadApi.upload(file);
      setValues((cur) => ({ ...cur, [id]: u.name }));
      setNote(`Image ready: ${file.name} (${u.width}×${u.height}).`);
      return u.name;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The image could not be uploaded.");
      throw e;
    }
  };
  const runOptions = (
    <>
      <LoraStack workflowId={workflowId} />
            <fieldset className="provider-choice">
        <legend className="small muted">Where to run</legend>
        <label className="check"><input type="radio" name="provider" checked={provider === "local"} onChange={() => { setProvider("local"); setConsentPreview(null); }} /> On this computer (ComfyUI)
          {providerState && <span className={`pill ${providerState.local.state === "ready" ? "good" : "warn"}`}>{providerState.local.state === "ready" ? "running" : "not running"}</span>}
        </label>
        <label className="check"><input type="radio" name="provider" checked={provider === "cloud"} onChange={() => setProvider("cloud")} /> Comfy Cloud
          {providerState && providerState.cloud.state !== "ready" && <span className="small muted"> ({providerState.cloud.message})</span>}
        </label>
      </fieldset>
      <p className="small muted cost-line">
        {provider === "cloud"
          ? "Comfy Cloud bills each run. Open Workflow has not verified the price, so no amount is shown."
          : "Runs on this computer. Open Workflow adds no per-run charge; electricity and time are yours."}
      </p>
      {consentPreview && (
        <section className="consent" role="region" aria-label="Review what is sent to Comfy Cloud">
          <h3 className="sub-h">Review before sending</h3>
          <p>This run will be sent to <strong>{consentPreview.host}</strong>. Nothing has been sent yet.</p>
          <ul className="plain">{consentPreview.sends.map((x) => <li key={x.item}>{x.detail}</li>)}</ul>
          <p className="small muted">Not sent: {consentPreview.does_not_send.join(", ")}.</p>
          <p className="small muted">{consentPreview.cost_note}</p>
          <label className="check"><input type="checkbox" checked={consentChecked} onChange={(e) => setConsentChecked(e.target.checked)} /> I agree to send this to Comfy Cloud.</label>
          <div className="actions">
            <button disabled={!consentChecked || starting} onClick={() => void startRun({ acknowledged: true, digest: consentPreview.digest })}>Send to Comfy Cloud</button>
            <button className="secondary" onClick={() => setConsentPreview(null)}>Cancel</button>
          </div>
        </section>
      )}
    </>
  );

  if (mode === "use") return (
    <Studio workflowId={workflowId} spec={spec} verdicts={verdicts} values={values} setValues={setValues} onUpload={onUpload}
      startRun={() => void startRun()} runReason={runReason} busy={starting || active} runOptions={runOptions}
      runJob={runJob} active={active} cancelRun={() => void cancelRun()} runErr={runErr} err={err} note={note}
      saved={view.source === "saved"} blocked={view.readiness === "blocked" ? (view.reasons[0] ?? "see the analysis") : null}
      onEdit={onEdit} onReuse={reuse} />
  );

  return (
    <div className="builder">
      {err && <div className="banner bad" role="alert">{err}</div>}
      {note && <div className="banner info" role="status">{note}</div>}
      {view.stale && <div className="banner warn" role="status">This saved app was made for an earlier copy of the workflow. Regenerate it to match the current file.</div>}
      {view.readiness === "blocked" && <div className="banner warn" role="status">The workflow itself is blocked ({view.reasons[0]}). You can design the app, but it cannot run until that is fixed.</div>}
      <div className="toolbar">
        <span className={`pill ${v.cls}`} role="status">{v.text}</span>
        <span className="small muted">{view.source === "saved" ? `Saved, revision ${view.revision}${dirty ? " · unsaved changes" : ""}` : "Generated by Forge, not saved yet"}</span>
        <label className="inline-label">Layout
          <select value={spec.layout} onChange={(e) => edit((s) => ({ ...s, layout: e.target.value as UISpec["layout"] }))}>
            {view.layouts.map((l) => <option key={l} value={l}>{LAYOUT_TEXT[l] ?? l}</option>)}
          </select>
        </label>
        <button disabled={busy || !dirty} onClick={() => void run(async () => {
          const r = await forgeApi.saveSpec(workflowId, spec, view.revision);
          setSaved(JSON.stringify(spec)); setView({ ...view, revision: r.revision, source: "saved" }); setValidation(r.validation); setNote("Saved.");
        })}>Save app</button>
        <button className="secondary" disabled={busy} onClick={() => void run(async () => { adopt(await forgeApi.regenerate(workflowId), false); setNote("Regenerated from the workflow. Save to keep it."); })}>Regenerate</button>
        {view.source === "saved" && (
          <button className="secondary" disabled={busy || dirty} title={dirty ? "Save your changes first" : "Adds a 'why' note to controls that have none, keeping your edits"}
            onClick={() => void run(async () => {
              const r = await forgeApi.addReasons(workflowId);
              adopt(await forgeApi.spec(workflowId), true);
              setNote(r.changed.length ? `Added the reason to ${r.changed.length} control${r.changed.length > 1 ? "s" : ""}. Your edits are kept.` : "Every control already has its reason.");
            })}>Add reasons</button>
        )}
      </div>

      <ModelPanel
        workflowId={workflowId}
        draft={spec}
        onProposal={(next, v) => {
          setSpec(next); setValidation(v); setNote("Proposal applied to the draft. Review it, then save.");
          setValues((cur) => ({ ...defaultValues(next), ...cur }));
        }}
      />

      <div className="builder-grid">
        <section aria-labelledby="prev-h">
          <h3 id="prev-h" className="sub-h">Preview</h3>
          <div className="preview-frame">
            <Renderer spec={spec} verdicts={verdicts} values={values} onChange={(id, val) => setValues((cur) => ({ ...cur, [id]: val }))}
              onUpload={onUpload}
              onRun={() => void startRun()} runReason={runReason} runBusy={starting || active} />
            {runOptions}
          </div>
          {runErr && <div className="banner bad" role="alert">{runErr}</div>}
          {runJob && (
            <div className="run-panel" role="status" aria-live="polite">
              <div className="run-head">
                <strong>{RUN_TEXT[runJob.state] ?? runJob.state}</strong>
                {active && <button className="secondary" onClick={() => void cancelRun()}>Cancel</button>}
              </div>
              <p className="small muted">Ran on: {runJob.provider === "cloud" ? "Comfy Cloud (after your consent)" : "this computer (ComfyUI)"}</p>
              {active && <div className="indeterminate" aria-hidden="true" />}
              {runJob.error && <p className="warn-text">{runJob.error}</p>}
              {runJob.notes.map((n) => <p key={n} className="small muted">{n}</p>)}
              {runJob.state === "COMPLETED" && runJob.hasOutput && (
                <button type="button" className="run-output-btn" aria-label="Open the output at full size"
                  onClick={() => setExpanded({ src: forgeApi.runOutputUrl(runJob.id), label: "Latest run" })}>
                  <img className="run-output" src={forgeApi.runOutputUrl(runJob.id)} alt="Output of the latest run" />
                </button>
              )}
              {runJob.state === "COMPLETED" && (
                <span className="run-actions">
                  {runJob.hasOutput && <a className="small" href={forgeApi.runOutputUrl(runJob.id)} download>Download the image</a>}
                  <button type="button" className="secondary small-btn" aria-expanded={showReceipt} onClick={() => setShowReceipt((v) => !v)}>{showReceipt ? "Hide receipt" : "View receipt"}</button>
                </span>
              )}
              {runJob.state === "COMPLETED" && showReceipt && <RunReceipt jobId={runJob.id} />}
            </div>
          )}
        </section>
        <section aria-labelledby="insp-h">
          <h3 id="insp-h" className="sub-h">Controls ({spec.components.length})</h3>
          <ul className="inspector">
            {spec.components.map((c, i) => {
              const r = verdictOf.get(c.id);
              return (
                <li key={c.id} className={`insp-item ${r?.status ?? ""}`}>
                  <div className="insp-head">
                    <input aria-label={`Label for ${c.binding.input}`} value={c.label} maxLength={80} onChange={(e) => edit((s) => { s.components[i].label = e.target.value || c.label; return s; })} />
                    <span className={`pill ${r?.status === "verified" ? "good" : r?.status === "invalid" ? "bad" : "warn"}`}>{r?.status ?? "…"}</span>
                  </div>
                  <div className="small muted">{c.type.replace("_", " ")} · bound to <code>{c.binding.node}.{c.binding.input}</code></div>
                  {r && r.reasons.length > 0 && <ul className="small warn-text">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
                  <div className="insp-actions">
                    {c.type === "image_upload" && (
                      <label className="inline-label">Image is for
                        <select value={c.role ?? ""} onChange={(e) => edit((s) => { s.components[i].role = (e.target.value || null) as UISpec["components"][number]["role"]; return s; })}>
                          <option value="">Not set</option>
                          <option value="identity">Identity (who)</option>
                          <option value="layout">Layout (pose or composition)</option>
                          <option value="garment">Garment (what they wear)</option>
                          <option value="style">Style</option>
                          <option value="other">Other</option>
                        </select>
                      </label>
                    )}
                    <label className="check"><input type="checkbox" checked={!c.hidden} onChange={(e) => edit((s) => { s.components[i].hidden = !e.target.checked; return s; })} /> Show</label>
                    <label className="inline-label">Panel
                      <select value={c.group} onChange={(e) => edit((s) => { s.components[i].group = e.target.value as "main" | "advanced"; return s; })}>
                        <option value="main">Main</option><option value="advanced">Advanced</option>
                      </select>
                    </label>
                    <button className="ghost" disabled={i === 0} onClick={() => edit((s) => { [s.components[i - 1], s.components[i]] = [s.components[i], s.components[i - 1]]; return s; })} aria-label={`Move ${c.label} up`}>↑</button>
                    <button className="ghost" disabled={i === spec.components.length - 1} onClick={() => edit((s) => { [s.components[i + 1], s.components[i]] = [s.components[i], s.components[i + 1]]; return s; })} aria-label={`Move ${c.label} down`}>↓</button>
                  </div>
                </li>
              );
            })}
          </ul>
          {validation && validation.problems.length > 0 && <ul className="warnings">{validation.problems.map((p) => <li key={p} className="w-error">{p}</li>)}</ul>}
        </section>
      </div>
      {expanded && <Lightbox src={expanded.src} label={expanded.label} onClose={() => setExpanded(null)} />}
      <RunHistory workflowId={workflowId} refreshKey={runJob?.state ?? ""} onReuse={reuse} onExpand={setExpanded} />
      <BeforeAfter workflowId={workflowId} values={values} spec={spec} latest={runJob} />
    </div>
  );
}

/** Ask a local model for a layout. The result is a proposal: it goes to the draft only when the user applies it. */
function ModelPanel({ workflowId, draft, onProposal }: { workflowId: string; draft: UISpec; onProposal: (next: UISpec, v: SpecValidation) => void }) {
  const [status, setStatus] = useState<LlmStatus | null>(null);
  const [model, setModel] = useState("");
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProposalResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    forgeModelApi.status().then((s) => { setStatus(s); setModel(s.default_model ?? ""); })
      .catch(() => setStatus({ state: "unreachable", url: "", models: [], reason: "Could not check the local model.", default_model: null, timeout_seconds: 0 }));
  }, []);

  const ask = async () => {
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await forgeModelApi.propose(workflowId, { request: request.trim(), model: model || null, current_spec: draft }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The model could not make a proposal.");
    } finally { setBusy(false); }
  };

  const ready = status?.state === "ok";
  const changed = result?.changed.length ?? 0;
  return (
    <details className="model-panel">
      <summary>Design with a local model <span className="small muted">optional</span></summary>
      <div className="model-body">
        {status === null && <div className="skeleton" aria-busy="true" style={{ height: 40 }} />}
        {status && !ready && (
          <p className="banner warn" role="status">
            {status.reason ?? "No local model is available."} Forge still works without one: you can edit the layout by hand.
          </p>
        )}
        {ready && (
          <>
            <p className="small muted">
              The model only suggests labels, types, panels and what to hide. Each suggestion is checked against this workflow before you see it,
              and nothing is saved until you do. The first request can take about half a minute while the model loads.
            </p>
            <div className="model-form">
              <label>Model
                <select value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
                  {status.models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="grow">What should this app be for?
                <textarea rows={2} maxLength={600} value={request} onChange={(e) => setRequest(e.target.value)} disabled={busy}
                  placeholder="For example: a simple portrait tool for my team. Keep the prompt and seed at the top." />
              </label>
              <button onClick={() => void ask()} disabled={busy || !request.trim()}>{busy ? "Asking the model…" : result ? "Ask again" : "Propose a layout"}</button>
            </div>
          </>
        )}
        {err && <div className="banner bad" role="alert">{err}</div>}
        {result && (
          <div className="proposal" role="region" aria-label="Proposal">
            <div className="pills">
              <span className={`pill ${VALIDATION[result.validation.status].cls}`}>{VALIDATION[result.validation.status].text}</span>
              <span className="pill muted">{changed} control{changed === 1 ? "" : "s"} changed</span>
              <span className="pill muted">Layout: {LAYOUT_TEXT[result.spec.layout] ?? result.spec.layout}</span>
            </div>
            <p><strong>{result.spec.name}</strong>{result.spec.description && <span className="muted"> · {result.spec.description}</span>}</p>
            {result.warnings.length > 0 && <ul className="warnings">{result.warnings.map((w) => <li key={w} className="w-warn">{w}</li>)}</ul>}
            {result.validation.status === "invalid" && (
              <p className="warn-text small">This proposal breaks a control's link to the workflow, so it cannot be applied. Ask again with a clearer request.</p>
            )}
            <div className="actions">
              <button disabled={result.validation.status === "invalid"} onClick={() => { onProposal(result.spec, result.validation); setResult(null); }}>Apply to draft</button>
              <button className="secondary" onClick={() => setResult(null)}>Discard</button>
            </div>
          </div>
        )}
      </div>
    </details>
  );
}

type RunRow = { id: string; state: string; provider: string; created_at: string | null; has_output: boolean; error: string | null; receipt_url: string | null };

/** Every run of this app, newest first, with a thumbnail, its receipt, and a way to reuse its values. */
export function RunHistory({ workflowId, refreshKey, onReuse, onExpand }: {
  workflowId: string; refreshKey: string; onReuse?: (values: Record<string, unknown>) => void;
  onExpand?: (view: { src: string; label: string }) => void;
}) {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [reusing, setReusing] = useState<string | null>(null);
  const [tick, setTick] = useState(0);   // bumped after a replay so the new run shows at once
  const [busyRun, setBusyRun] = useState<string | null>(null);
  const [cmp, setCmp] = useState<{ a: string; b: string; data: RunCompare } | null>(null);
  const [runMsg, setRunMsg] = useState<string | null>(null);

  // Runs the app again with the exact receipt values, and says so if the spec has changed since.
  const replay = async (id: string) => {
    setBusyRun(id); setRunMsg(null);
    try {
      const r = await forgeRunsApi.replay(id);
      setRunMsg(`Run again started${r.spec_changed ? ". The app's controls have changed since this run, so check the values" : ""}.`);
      setTick((t) => t + 1);
    } catch (e) {
      setRunMsg(e instanceof Error ? e.message : "The run could not be repeated.");
    } finally {
      setBusyRun(null);
    }
  };

  const compareWith = async (a: string, b: string) => {
    setRunMsg(null);
    try {
      setCmp({ a, b, data: await forgeRunsApi.compare(a, b) });
    } catch (e) {
      setRunMsg(e instanceof Error ? e.message : "The runs could not be compared.");
    }
  };
  useEffect(() => {
    let alive = true;
    forgeRunsApi.list(workflowId).then((r) => { if (alive) setRuns(r.runs); }).catch(() => { if (alive) setRuns([]); });
    return () => { alive = false; };
  }, [workflowId, refreshKey, tick]);

  // The receipt keeps the exact values the run used, so reusing them never guesses.
  const reuse = async (id: string) => {
    if (!onReuse) return;
    setReusing(id);
    try {
      const res = await fetch(forgeApi.runReceiptUrl(id));
      if (!res.ok) throw new Error("The receipt could not be read.");
      const receipt = (await res.json()) as { values?: Record<string, unknown> };
      onReuse(receipt.values ?? {});
    } catch {
      onReuse({});
    } finally {
      setReusing(null);
    }
  };

  return (
    <section className="run-history" aria-labelledby="rh-h">
      <h3 id="rh-h" className="sub-h">Runs of this app</h3>
      {runs === null && <div className="skeleton" aria-busy="true" style={{ height: 48 }} />}
      {runs && runs.length === 0 && <p className="muted small">No runs yet. Your runs appear here with their receipts.</p>}
      {runMsg && <p className="small" role="status">{runMsg}</p>}
      {runs && runs.length > 0 && (
        <ul className="run-list">
          {runs.map((r) => (
            <li key={r.id} className="run-item">
              {r.state === "COMPLETED" && r.has_output && (() => {
                const when = r.created_at ? new Date(r.created_at).toLocaleString() : "earlier";
                return (
                  <button type="button" className="run-thumb-btn" aria-label={`Open the output of the run from ${when}`}
                    onClick={() => onExpand?.({ src: forgeApi.runOutputUrl(r.id), label: `Run from ${when}` })}>
                    <img className="run-thumb" src={forgeApi.runOutputUrl(r.id)} alt="" loading="lazy" />
                  </button>
                );
              })()}
              <div className="run-meta">
                <span className={`pill ${r.state === "COMPLETED" ? "good" : r.state === "FAILED" ? "bad" : "muted"}`}>{RUN_TEXT[r.state] ?? r.state}</span>
                <span className="small muted">{r.created_at ? new Date(r.created_at).toLocaleString() : ""} · {r.provider === "cloud" ? "Comfy Cloud" : "this computer"}</span>
                {r.error && <span className="small warn-text">{r.error}</span>}
                <span className="run-actions">
                  {r.state === "COMPLETED" && r.has_output && <a className="small" href={forgeApi.runOutputUrl(r.id)} download>Download image</a>}
                  {r.receipt_url && <a className="small" href={r.receipt_url} download>Download receipt</a>}
                  {r.receipt_url && <a className="small" href={r.receipt_url} target="_blank" rel="noopener noreferrer">View receipt</a>}
                  {onReuse && r.receipt_url && (
                    <button type="button" className="ghost small-btn" onClick={() => void reuse(r.id)} disabled={reusing === r.id}>
                      {reusing === r.id ? "Loading…" : "Use these values"}
                    </button>
                  )}
                  {r.receipt_url && (
                    <button type="button" className="small-btn" onClick={() => void replay(r.id)} disabled={busyRun !== null}
                      title="Runs again with exactly this run's values, seed and LoRAs">
                      {busyRun === r.id ? "Starting…" : "Run again exactly"}
                    </button>
                  )}
                  {r.receipt_url && runs.indexOf(r) < runs.length - 1 && (
                    <button type="button" className="ghost small-btn" onClick={() => void compareWith(r.id, runs[runs.indexOf(r) + 1].id)}>
                      Compare with the run before
                    </button>
                  )}
                </span>
              </div>
              {cmp?.a === r.id && (
                <div className="run-compare" role="region" aria-label="Comparison">
                  <p className="small">
                    {cmp.data.same_app ? "Same app" : "Different apps"} · {cmp.data.same_workflow_file ? "same workflow file" : "the workflow file changed"}
                    {" · "}{cmp.data.loras_same ? "same LoRAs" : "LoRAs differ"}
                  </p>
                  {cmp.data.values.length === 0 ? <p className="small muted">Every value is the same.</p> : (
                    <ul className="small">
                      {cmp.data.values.map((v) => (
                        <li key={v.name}><code>{v.name}</code>: {String(v.b ?? "—")} → {String(v.a ?? "—")}</li>
                      ))}
                    </ul>
                  )}
                  <p className="small muted">
                    This run: {cmp.data.repeatable.a.repeatable ? "can be repeated exactly" : `cannot be repeated exactly (${cmp.data.repeatable.a.why.join("; ")})`}.
                    {" "}Run before: {cmp.data.repeatable.b.repeatable ? "can be repeated exactly" : `cannot be repeated exactly (${cmp.data.repeatable.b.why.join("; ")})`}.
                  </p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Input image beside the output, with a draggable divider. Shown only when the app has an image input and an output. */
function BeforeAfter({ workflowId, values, spec, latest }: { workflowId: string; values: Values; spec: UISpec; latest: RunState | null }) {
  const [split, setSplit] = useState(50);
  const input = spec.components.find((c) => c.type === "image_upload" && typeof values[c.id] === "string");
  const inputName = input ? String(values[input.id]) : null;
  const [outputId, setOutputId] = useState<string | null>(null);
  useEffect(() => {
    if (latest?.state === "COMPLETED" && latest.hasOutput) { setOutputId(latest.id); return; }
    let alive = true;
    forgeRunsApi.list(workflowId).then((r) => { if (alive) setOutputId(r.runs.find((x) => x.has_output)?.id ?? null); }).catch(() => undefined);
    return () => { alive = false; };
  }, [workflowId, latest?.state, latest?.id]);
  if (!inputName || !outputId) return null;
  return (
    <section className="before-after" aria-label="Before and after">
      <h3 className="sub-h">Before and after</h3>
      <div className="ba-frame" style={{ ["--split" as string]: `${split}%` }}>
        <img className="ba-img" src={`/api/forge/uploads/${inputName}`} alt="Input image" />
        <img className="ba-img ba-top" src={forgeApi.runOutputUrl(outputId)} alt="Output image" />
      </div>
      <label className="small">Divider <input type="range" min={0} max={100} value={split} onChange={(e) => setSplit(Number(e.target.value))} aria-label="Divider position" /></label>
    </section>
  );
}

/** A full-size view of one output. Escape or the Close button leaves it; focus goes back to where the user was. */
export function Lightbox({ src, label, onClose }: { src: string; label: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); before?.focus?.(); };
  }, [onClose]);
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={`Output: ${label}`}>
      <div className="lightbox-bar">
        <span className="small">{label}</span>
        <span className="run-actions">
          <a className="small" href={src} download>Download image</a>
          <button type="button" className="ghost small-btn" ref={closeRef} onClick={onClose}>Close</button>
        </span>
      </div>
      <img className="lightbox-img" src={src} alt={`Output: ${label}`} />
    </div>
  );
}


type StudioProps = {
  workflowId: string; spec: UISpec; verdicts: Parameters<typeof Renderer>[0]["verdicts"]; values: Values;
  setValues: (fn: (cur: Values) => Values) => void; onUpload: (id: string, file: File) => Promise<string>;
  startRun: () => void; runReason: string | null; busy: boolean; runOptions: React.ReactNode;
  runJob: RunState | null; active: boolean; cancelRun: () => void; runErr: string | null; err: string | null; note: string | null;
  saved: boolean; blocked: string | null; onEdit?: () => void; onReuse: (values: Record<string, unknown>) => void;
};

/** The app as people use it: controls on the left, a large result on the right, and every run below. */
function Studio(p: StudioProps) {
  const [expanded, setExpanded] = useState<{ src: string; label: string } | null>(null);
  const [coverMissing, setCoverMissing] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);
  const done = p.runJob?.state === "COMPLETED" && p.runJob.hasOutput;
  return (
    <div className="studio">
      {p.err && <div className="banner bad" role="alert">{p.err}</div>}
      {p.note && <div className="banner info" role="status">{p.note}</div>}
      {!p.saved && (
        <div className="banner info" role="status">
          This app is not saved yet, so it cannot run.{" "}
          {p.onEdit && <button type="button" className="link" onClick={p.onEdit}>Review and save it</button>}
        </div>
      )}
      {p.blocked && <div className="banner warn" role="status">This workflow is blocked: {p.blocked}</div>}
      <div className="studio-grid">
        <aside className="studio-controls" aria-label="Controls">
          <Renderer spec={p.spec} verdicts={p.verdicts} values={p.values} onChange={(id, v) => p.setValues((cur) => ({ ...cur, [id]: v }))}
            onUpload={p.onUpload} onRun={p.startRun} runReason={p.runReason} runBusy={p.busy} compact />
          {p.runOptions}
          {p.runErr && <div className="banner bad" role="alert">{p.runErr}</div>}
        </aside>
        <section className="studio-stage" aria-label="Result" aria-live="polite">
          {p.runJob && !done && (
            <div className="stage-status">
              <strong>{RUN_TEXT[p.runJob.state] ?? p.runJob.state}</strong>
              {p.active && <div className="indeterminate" aria-hidden="true" />}
              {p.runJob.error && <p className="warn-text">{p.runJob.error}</p>}
              {p.active && <button type="button" className="secondary" onClick={p.cancelRun}>Cancel</button>}
            </div>
          )}
          {done && p.runJob ? (
            <>
              <button type="button" className="stage-image-btn" aria-label="Open the result at full size"
                onClick={() => setExpanded({ src: forgeApi.runOutputUrl(p.runJob!.id), label: "Latest run" })}>
                <img className="stage-image" src={forgeApi.runOutputUrl(p.runJob.id)} alt="Result of the latest run" />
              </button>
              <div className="run-actions stage-actions">
                <a className="small" href={forgeApi.runOutputUrl(p.runJob.id)} download>Download the image</a>
                <button type="button" className="secondary small-btn" aria-expanded={showReceipt} onClick={() => setShowReceipt((v) => !v)}>{showReceipt ? "Hide receipt" : "View receipt"}</button>
              </div>
              {showReceipt && <RunReceipt jobId={p.runJob.id} />}
            </>
          ) : !p.runJob && !coverMissing ? (
            <figure className="stage-cover">
              <img className="stage-image" src={coverUrl(p.workflowId)} alt="The latest result of this app" onError={() => setCoverMissing(true)} />
              <figcaption className="small muted">Latest result. Press Generate for a new one.</figcaption>
            </figure>
          ) : !p.runJob ? (
            <div className="stage-empty">
              <p>Your result appears here.</p>
              <p className="small muted">Set the controls on the left, then press Generate.</p>
            </div>
          ) : null}
        </section>
      </div>
      {expanded && <Lightbox src={expanded.src} label={expanded.label} onClose={() => setExpanded(null)} />}
      <RunHistory workflowId={p.workflowId} refreshKey={p.runJob?.state ?? ""} onReuse={p.onReuse} onExpand={setExpanded} />
    </div>
  );
}
