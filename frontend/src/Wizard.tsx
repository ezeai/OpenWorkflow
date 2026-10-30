import { useEffect, useState } from "react";
import { api, ApiError, type Hardware, type ModelsResponse, type Recommendation, type WizardState, type WorkflowEval } from "./api";

const TITLES: Record<string, string> = {
  welcome: "Welcome",
  system_check: "Connect to ComfyUI",
  model_discovery: "Check your models",
  workflow_readiness: "Workflow readiness",
  starter: "Starter workflow",
  download: "Optional download",
  completion: "You’re set up",
};

const STATUS_TEXT: Record<WorkflowEval["status"], string> = {
  validated: "Validated on this machine",
  requirements_met_untested: "Files and nodes present. Not run yet.",
  unconfirmed_files: "Needs you to confirm the files it uses",
  missing_models: "Missing required model files",
  missing_nodes: "Needs ComfyUI nodes that are not installed",
  missing_packs: "Needs a custom-node pack that is not installed",
  license_blocked: "Blocked: license restricts this use",
  unknown_engine: "Set your ComfyUI folder to check this",
};

export function Wizard({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState<WizardState | null>(null);
  const [step, setStep] = useState<string>("welcome");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.wizard().then((s) => {
      setState(s);
      const next = s.steps.find((x) => !s.completed.includes(x) && !s.skipped.includes(x));
      setStep(next ?? "completion");
    }).catch((e: Error) => setError(e.message));
  }, []);

  if (!state) return <div className="page"><div className="skeleton" aria-busy="true" style={{ height: 160 }} /></div>;

  const idx = state.steps.indexOf(step);
  const finished = (s: string) => state.completed.includes(s) || state.skipped.includes(s);

  async function advance(action: "complete" | "skip", workflowId?: string | null) {
    setBusy(true);
    setError(null);
    try {
      const r = await api.wizardStep(step, action, workflowId);
      setState((prev) => prev && { ...prev, completed: r.completed, skipped: r.skipped });
      const next = state!.steps[idx + 1];
      if (next) setStep(next);
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "The step could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page wizard">
      <nav className="steps" aria-label="Setup progress">
        <ol>
          {state.steps.map((s) => (
            <li key={s}>
              <button className={s === step ? "step active" : "step"} aria-current={s === step ? "step" : undefined} onClick={() => setStep(s)}>
                <span className="dot">{state.completed.includes(s) ? "✓" : state.skipped.includes(s) ? "–" : ""}</span>
                {TITLES[s]}
              </button>
            </li>
          ))}
        </ol>
        <p className="small muted">Every step is optional. You can leave setup at any point and return from Settings.</p>
      </nav>

      <section className="wizard-body" aria-labelledby="wiz-title">
        <h1 id="wiz-title">{TITLES[step]}</h1>
        {step === "welcome" && <Welcome />}
        {step === "system_check" && <SystemCheck />}
        {step === "model_discovery" && <ModelDiscovery />}
        {step === "workflow_readiness" && <WorkflowReadiness />}
        {step === "starter" && <Starter onChoose={(id) => advance("complete", id)} busy={busy} />}
        {step === "download" && <DownloadStep />}
        {step === "completion" && <Completion state={state} onOpen={onDone} />}

        {error && <div className="banner bad" role="alert">{error}</div>}

        <div className="actions wizard-actions">
          {idx > 0 && <button className="ghost" onClick={() => setStep(state.steps[idx - 1])}>Back</button>}
          {step !== "starter" && step !== "completion" && step !== "download" && !finished(step) && (
            <button className="secondary" disabled={busy} onClick={() => advance("complete")}>
              {busy ? "Checking…" : step === "welcome" ? "Start setup" : "Continue"}
            </button>
          )}
          {step !== "welcome" && step !== "completion" && !finished(step) && (
            <button className="ghost" disabled={busy} onClick={() => advance("skip")}>Skip for now</button>
          )}
        </div>
      </section>
    </div>
  );
}

function Welcome() {
  return (
    <>
      <p className="lede">Open Workflow is a local-first studio. Generation with local workflows runs on this computer, using model files you have on disk or have chosen to download.</p>
      <ul className="plain">
        <li>This setup checks your hardware and looks for models. It downloads nothing unless you confirm a download.</li>
        <li>In this build, local workflows only contact ComfyUI on this computer. Nothing is uploaded to a remote service.</li>
        <li>No remote integration is included in this build.</li>
      </ul>
    </>
  );
}

function SystemCheck() {
  const [hw, setHw] = useState<Hardware | null>(null);
  const [engine, setEngine] = useState<string>("checking…");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.hardware().then(setHw).catch((e: Error) => setErr(e.message));
    api.health().then((h) => setEngine(h.engine.state === "ok" ? `ComfyUI ${h.engine.comfyui_version ?? ""} is running` : "ComfyUI is not running on the configured address")).catch(() => setEngine("unavailable"));
  }, []);
  const gpu = hw?.gpus[0];
  return (
    <>
      <p className="small muted">Live readings from this computer, taken just now.</p>
      {err && <div className="banner bad" role="alert">Hardware could not be read: {err}</div>}
      {!hw && !err && <div className="skeleton" aria-busy="true" style={{ height: 120 }} />}
      {hw && (
        <dl className="status-list">
          <dt>GPU</dt><dd>{gpu ? `${gpu.name}, ${(gpu.vram_free_mb / 1024).toFixed(1)} of ${(gpu.vram_total_mb / 1024).toFixed(0)} GB VRAM free` : "No NVIDIA GPU detected. Generation would run on the CPU, which is not supported for these workflows."}</dd>
          <dt>Memory</dt><dd>{hw.ram_available_gb} of {hw.ram_total_gb} GB available</dd>
          <dt>Free space</dt><dd>{Object.entries(hw.disk_free_gb).map(([p, g]) => `${g ?? "unknown"} GB at ${p}`).join("; ")}</dd>
          <dt>FFmpeg</dt><dd>{hw.ffmpeg.available ? "Found" : "Not found. Video import and comparison will be limited."}</dd>
          <dt>ComfyUI</dt><dd>{engine}</dd>
        </dl>
      )}
      {gpu && gpu.vram_free_mb < 8 * 1024 && (
        <p className="warn-text">Less than 8 GB of GPU memory is free. Other programs may be using the GPU. Close them if a generation fails, and Open Workflow will not stop them for you.</p>
      )}
    </>
  );
}

function ModelDiscovery() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scan = async () => {
    setScanning(true);
    setErr(null);
    try {
      await api.scan();
      setData(await api.models());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Scan failed.");
    } finally {
      setScanning(false);
    }
  };
  useEffect(() => { void scan(); }, []);
  return (
    <>
      <p>Open Workflow looks in your ComfyUI model folders and any folders you have added in Settings. It reads file names and headers only, and moves, copies and renames nothing.</p>
      <div className="actions"><button className="secondary" onClick={scan} disabled={scanning}>{scanning ? "Scanning…" : "Scan again"}</button></div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {data && (
        <>
          <p className="small">{data.scan.files_found} model-type file(s) seen. Roots: {data.scan.roots.map((r) => r.label).join(", ")}.</p>
          <ul className="plain">
            {data.models.map((m) => (
              <li key={m.id}><strong>{m.name}</strong>: {m.category === "missing" ? "not found" : m.category === "discovered" ? "found, identity not confirmed yet" : m.category === "installed" ? "identity confirmed" : "managed by Open Workflow"}</li>
            ))}
          </ul>
          <p className="small muted">Finding a file does not make it usable. The Model Library shows what needs your confirmation.</p>
        </>
      )}
    </>
  );
}

function WorkflowReadiness() {
  const [evals, setEvals] = useState<WorkflowEval[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.workflows().then((r) => setEvals(r.workflows)).catch((e: Error) => setErr(e.message)); }, []);
  if (err) return <div className="banner bad" role="alert">{err}</div>;
  if (!evals) return <div className="skeleton" aria-busy="true" style={{ height: 120 }} />;
  return (
    <ul className="wf-list">
      {evals.map((w) => (
        <li key={w.id} className="card">
          <strong>{w.name}</strong>
          <div className="small">{STATUS_TEXT[w.status]}</div>
          {w.blockers.map((b) => <div key={b} className="warn-text small">{b}</div>)}
          {w.missing_models.length > 0 && <div className="small muted">Missing: {w.missing_models.map((m) => m.role ? `${m.role} (${m.model_id})` : m.model_id).join(", ")}</div>}
          {w.missing_packs.length > 0 && <div className="small muted">Install: {w.missing_packs.join(", ")}</div>}
          <div className="small muted">Not validated and not smoke-tested. Unsupported here: {w.unsupported_controls.join(", ") || "none declared"}.</div>
        </li>
      ))}
    </ul>
  );
}

function Starter({ onChoose, busy }: { onChoose: (id: string | null) => void; busy: boolean }) {
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    api.recommendation().then(setRec).catch(() => setRec(null));
    api.workflows().then((r) => setName(r.workflows.find((w) => w.id === rec?.workflow_id)?.name ?? null)).catch(() => setName(null));
  }, [rec?.workflow_id]);
  if (!rec) return <div className="skeleton" aria-busy="true" style={{ height: 80 }} />;
  if (!rec.workflow_id) {
    return (
      <>
        <p>No workflow is ready yet. {rec.reason}</p>
        {rec.blockers && rec.blockers.length > 0 && <ul className="plain">{rec.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
        {rec.next_steps && rec.next_steps.length > 0 && <ul className="plain">{rec.next_steps.map((n) => <li key={n}>{n}</li>)}</ul>}
        <div className="actions"><button className="secondary" disabled={busy} onClick={() => onChoose(null)}>Continue without a starter</button></div>
      </>
    );
  }
  return (
    <>
      <p>Closest workflow to ready: <strong>{name ?? rec.workflow_id}</strong>.</p>
      <p className="small muted">{rec.reason} Choosing it does not confirm any file or run anything.</p>
      <div className="actions"><button disabled={busy} onClick={() => onChoose(rec.workflow_id)}>Use this starter</button></div>
    </>
  );
}

function DownloadStep() {
  return (
    <>
      <p>No verified download source is recorded for the candidate models yet. Open Workflow only offers downloads from publisher sources whose file list has been checked, and none has been yet.</p>
      <p className="small muted">If you already have the files, skip this step and confirm them in the Model Library.</p>
    </>
  );
}

function Completion({ state, onOpen }: { state: WizardState; onOpen: () => void }) {
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { api.recommendation().then(setRec).catch(() => setRec(null)); }, []);
  const finish = async () => {
    try {
      await api.wizardStep("completion", "complete");
      onOpen();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Setup could not be finished.");
    }
  };
  return (
    <>
      <p>Setup summary. {state.completed.length} step(s) completed, {state.skipped.length} skipped.</p>
      <ul className="plain">
        <li>Starter workflow: {state.starter_workflow ?? "none chosen"}</li>
        <li>Next recommended action: {rec?.workflow_id ? `Confirm the files for ${rec.workflow_id} in the Model Library.` : "Add or point to model folders in Settings, then rescan."}</li>
      </ul>
      <p className="small muted">Setup is not forced on the next launch. Run it again from Settings at any time.</p>
      {msg && <div className="banner warn" role="alert">{msg} You can skip those steps, then finish.</div>}
      <div className="actions"><button onClick={finish}>Open the home dashboard</button></div>
    </>
  );
}
