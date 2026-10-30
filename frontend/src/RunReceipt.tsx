import { useEffect, useState } from "react";
import { forgeApi } from "./api";
import { StatusBadge } from "./ui/primitives";

type Receipt = {
  workflow_name?: string; spec_revision?: number; app_version?: string; provider?: string; job_id?: string; completed_at?: string;
  output_sha256?: string; comfy_filename?: string; values?: Record<string, unknown>; limitations?: string[];
  node_definitions?: { comfyui_version?: string | null };
  models?: { role: string; file: string; status: string }[];
};

const show = (v: unknown): string => (typeof v === "string" ? (v.length > 90 ? `${v.slice(0, 90)}…` : v) : JSON.stringify(v));

/** What a run actually executed, read from the saved receipt. Only fields present in the receipt are shown. */
export function RunReceipt({ jobId }: { jobId: string }) {
  const [r, setR] = useState<Receipt | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setR(null); setErr(null);
    fetch(forgeApi.runReceiptUrl(jobId))
      .then((res) => { if (!res.ok) throw new Error("The receipt could not be read."); return res.json(); })
      .then((d: Receipt) => { if (alive) setR(d); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [jobId]);

  if (err) return <p className="small warn-text" role="alert">{err}</p>;
  if (!r) return <div className="skeleton" aria-busy="true" style={{ height: 90 }} />;
  const values = Object.entries(r.values ?? {});

  return (
    <section className="receipt" aria-label="Run receipt">
      <p className="small muted">The receipt records what was actually executed for this run, so it can be checked or repeated later.</p>
      <dl className="receipt-grid">
        <div><dt>Status</dt><dd><StatusBadge tone="good">Completed</StatusBadge></dd></div>
        <div><dt>Run ID</dt><dd><code>{r.job_id ?? jobId}</code></dd></div>
        {r.workflow_name && <div><dt>App</dt><dd>{r.workflow_name}{r.spec_revision != null ? ` · app revision ${r.spec_revision}` : ""}</dd></div>}
        <div><dt>Ran on</dt><dd>{r.provider === "cloud" ? "Comfy Cloud (with your consent)" : "This computer (local ComfyUI)"}</dd></div>
        {r.completed_at && <div><dt>Finished</dt><dd>{new Date(r.completed_at).toLocaleString()}</dd></div>}
        {r.node_definitions?.comfyui_version && <div><dt>ComfyUI</dt><dd>{r.node_definitions.comfyui_version}</dd></div>}
        {r.app_version && <div><dt>Open Workflow</dt><dd>{r.app_version}</dd></div>}
        {r.output_sha256 && <div className="wide"><dt>Output SHA-256</dt><dd><code>{r.output_sha256}</code></dd></div>}
      </dl>
      <h4 className="receipt-sub">Inputs used</h4>
      {values.length === 0 ? <p className="small muted">This run used the workflow's own values.</p> : (
        <ul className="receipt-values">{values.map(([k, v]) => <li key={k}><span className="muted">{k}</span><span>{show(v)}</span></li>)}</ul>
      )}
      {(r.models?.length ?? 0) > 0 && (<>
        <h4 className="receipt-sub">Model files</h4>
        <ul className="receipt-values">{r.models!.map((m) => <li key={`${m.role}-${m.file}`}><span className="muted">{m.role}</span><span>{m.file}</span></li>)}</ul>
      </>)}
      {r.limitations?.map((l) => <p key={l} className="small muted">{l}</p>)}
      <a className="small" href={forgeApi.runReceiptUrl(jobId)} download>Download receipt (JSON)</a>
    </section>
  );
}
