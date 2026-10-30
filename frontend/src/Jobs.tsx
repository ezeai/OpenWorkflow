import { useCallback, useEffect, useState } from "react";
import { ApiError, jobsApi, ACTIVE_JOB_STATES, type JobView } from "./api";

const STATE_TEXT: Record<string, string> = {
  QUEUED: "Queued", VALIDATING: "Checking files and engine", LOADING_MODEL: "Loading model",
  RUNNING: "Running on ComfyUI", POSTPROCESSING: "Saving output", COMPLETED: "Completed",
  FAILED: "Failed", CANCELLING: "Cancelling…", CANCELLED: "Cancelled", INTERRUPTED: "Interrupted",
  RECOVERY_REQUIRED: "Output can be recovered",
};

function fmtElapsed(createdAt: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(createdAt).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function Jobs() {
  const [jobs, setJobs] = useState<JobView[] | null>(null);
  const [engine, setEngine] = useState<string>("checking…");
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([jobsApi.list(), jobsApi.status()]);
      setJobs(l.jobs);
      setEngine(s.engine_reachable ? "ComfyUI reachable" : "ComfyUI not reachable");
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load jobs.");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await load(); } catch (e) { setErr(e instanceof ApiError || e instanceof Error ? e.message : "Action failed."); }
  };

  return (
    <div className="page wide">
      <header className="page-head">
        <div>
          <p className="eyebrow">Library</p>
          <h1>Run History</h1>
          <p className="lede">Every generation, with its state and receipt. One runs at a time on this GPU; progress shows the node ComfyUI is running and, while sampling, the step count.</p>
        </div>
        <span className="save-status" role="status">{engine}</span>
      </header>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {jobs === null && <div className="skeleton" aria-busy="true" style={{ height: 120 }} />}
      {jobs && jobs.length === 0 && <section className="card empty"><h2>No jobs yet</h2><p className="muted">Generate a shot from the Director and it appears here.</p></section>}
      {jobs && jobs.length > 0 && (
        <ul className="project-list">
          {jobs.map((j) => {
            const active = ACTIVE_JOB_STATES.includes(j.state);
            return (
              <li key={j.id} className={`card job-row state-${j.state.toLowerCase()}`}>
                <div className="job-head">
                  <strong>{STATE_TEXT[j.state] ?? j.state}</strong>
                  <span className="small muted">{j.workflow_id} · seed {String(j.params.seed)} · {new Date(j.created_at).toLocaleString()}</span>
                </div>
                {active && (
                  <div className="state-line">
                    <span>Current step: <strong>{STATE_TEXT[j.state] ?? j.state}</strong></span>
                    {j.progress.node && <span>Node: <strong>{j.progress.node}</strong></span>}
                    {j.progress.mode === "determinate" && j.progress.step != null && j.progress.steps ? (
                      <span>Sampling step <strong>{j.progress.step} of {j.progress.steps}</strong></span>
                    ) : null}
                    <span>Elapsed {fmtElapsed(j.created_at)}</span>
                  </div>
                )}
                {active && j.progress.mode === "determinate" && j.progress.step != null && j.progress.steps ? (
                  <progress
                    className="job-progress"
                    max={j.progress.steps}
                    value={j.progress.step}
                    aria-label={`${j.progress.node ?? "Sampling"} step ${j.progress.step} of ${j.progress.steps}`}
                  />
                ) : active ? <div className="indeterminate" aria-label="Working" role="progressbar" /> : null}
                {j.error && <div className="banner bad small" role="alert">{j.error}</div>}
                {j.output_asset_id && <div className="small">Output saved as a project asset.</div>}
                {j.kind === "app" && j.output_file && (
                  <figure className="job-output">
                    <img src={`/api/forge/runs/${j.id}/output`} alt={`Output of run ${j.id.slice(0, 8)}`} loading="lazy" />
                    <figcaption className="small"><a href={`/api/forge/runs/${j.id}/receipt`}>Receipt</a></figcaption>
                  </figure>
                )}
                <div className="actions" style={{ marginTop: 6 }}>
                  {(j.state === "QUEUED" || j.state === "RUNNING" || j.state === "VALIDATING") && (
                    <button className="secondary" onClick={() => act(() => jobsApi.cancel(j.id))}>Cancel</button>
                  )}
                  {(j.state === "FAILED" || j.state === "CANCELLED" || j.state === "INTERRUPTED") && (
                    <button className="secondary" onClick={() => act(() => jobsApi.retry(j.id))}>Retry as a new take</button>
                  )}
                  {j.state === "RECOVERY_REQUIRED" && <button onClick={() => act(() => jobsApi.importOutput(j.id))}>Import recovered output</button>}
                  <button className="ghost" onClick={() => setOpen(open === j.id ? null : j.id)}>{open === j.id ? "Hide details" : "Details"}</button>
                </div>
                {open === j.id && (
                  <div className="small job-detail">
                    <div>Models: {j.models.map((m) => `${m.role}: ${m.file ?? m.name} (${m.status ?? (m.identity_confirmed ? "identity confirmed" : "identity not confirmed")})`).join("; ") || "none recorded"}</div>
                    {j.prompt && <div>Prompt: <code>{j.prompt.positive}</code></div>}
                    <ul>{j.history.map((h, i) => <li key={i}>{h.state}: {h.note} <span className="muted">({new Date(h.at).toLocaleTimeString()})</span></li>)}</ul>
                    {j.notes.map((n, i) => <div key={i} className="muted">{n}</div>)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
