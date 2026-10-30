import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type Category, type Job, type ModelView, type ModelsResponse, type Plan, type VariantView } from "./api";

const CATEGORY_LABEL: Record<Category, string> = {
  managed: "Managed by Open Workflow",
  installed: "Identified",
  discovered: "Found, not confirmed",
  missing: "Not found",
};

const ELIGIBILITY_TEXT: Record<ModelView["open_weight_eligibility"], string> = {
  eligible: "Open-weight eligible",
  review: "Review pending",
  not_eligible: "Not eligible",
};

const LICENSE_TEXT: Record<ModelView["license"]["status"], string> = {
  verified: "License verified",
  restricted: "License restricted",
  unverified: "License unverified",
};

const TASK_LABEL: Record<string, string> = {
  "text-to-image": "Text to image",
  "image-edit": "Image editing",
  "image-to-video": "Image to video",
};

function bytes(n: number | null | undefined): string {
  if (n == null) return "unknown";
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  return `${n} B`;
}

function Pill({ tone, children }: { tone: "good" | "warn" | "bad" | "muted" | "info"; children: React.ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

function eligibilityTone(e: ModelView["open_weight_eligibility"]): "good" | "warn" | "bad" {
  return e === "eligible" ? "good" : e === "review" ? "warn" : "bad";
}

function licenseTone(s: ModelView["license"]["status"]): "good" | "warn" | "bad" {
  return s === "verified" ? "good" : s === "restricted" ? "bad" : "warn";
}

export function ModelLibrary() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "all">("all");
  const [task, setTask] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (force = false) => {
    setLoading(true);
    try {
      if (force) await api.scan();
      setData(await api.models());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the model library.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(false); }, [load]);

  const tasks = useMemo(() => Array.from(new Set((data?.models ?? []).flatMap((m) => m.tasks))).sort(), [data]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.models ?? []).filter((m) =>
      (category === "all" || m.category === category) &&
      (task === "all" || m.tasks.includes(task)) &&
      (!q || m.name.toLowerCase().includes(q) || m.publisher.toLowerCase().includes(q) || m.id.includes(q)));
  }, [data, query, category, task]);

  const current = data?.models.find((m) => m.id === selected) ?? null;

  return (
    <div className="page wide">
      <header className="page-head">
        <div>
          <h1>Model Library</h1>
          <p className="lede">Model files found in the folders below. Open Workflow reads them where they are; nothing is downloaded without your say. Finding a file is not the same as confirming its licence or running it.</p>
        </div>
        <button className="secondary" onClick={() => void load(true)} disabled={loading}>
          {loading ? "Scanning…" : "Rescan folders"}
        </button>
      </header>

      {notice && <div className="banner info" role="status">{notice} <button className="link" onClick={() => setNotice(null)}>Dismiss</button></div>}

      {data && (data.catalog_errors.length > 0 || data.registry_errors.length > 0) && (
        <div className="banner warn" role="alert">
          Some records could not be read and were skipped: {[...data.catalog_errors, ...data.registry_errors].join("; ")}
        </div>
      )}

      <div className="toolbar" role="search">
        <input aria-label="Search models" placeholder="Search by name or publisher" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select aria-label="Filter by task" value={task} onChange={(e) => setTask(e.target.value)}>
          <option value="all">All tasks</option>
          {tasks.map((t) => <option key={t} value={t}>{TASK_LABEL[t] ?? t}</option>)}
        </select>
        <div className="segmented" role="group" aria-label="Filter by status">
          {(["all", "managed", "installed", "discovered", "missing"] as const).map((c) => (
            <button key={c} aria-pressed={category === c} className={category === c ? "seg active" : "seg"} onClick={() => setCategory(c)}>
              {c === "all" ? "All" : CATEGORY_LABEL[c]}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="banner bad" role="alert">
          {error} <button className="link" onClick={() => void load(false)}>Try again</button>
        </div>
      )}

      {loading && !data && (
        <div className="grid" aria-busy="true">
          {[0, 1, 2, 3].map((i) => <div key={i} className="card skeleton-card" />)}
        </div>
      )}

      {data && visible.length === 0 && (
        <section className="card empty">
          <h2>No models match</h2>
          <p className="muted">Clear the filters, or add the folder that holds your models in Settings.</p>
        </section>
      )}

      <div className="split">
        <div className="table-wrap">
          <table className="data model-table">
            <thead>
              <tr><th scope="col">Model</th><th scope="col">Type</th><th scope="col">Licence</th><th scope="col">State</th></tr>
            </thead>
            <tbody>
              {visible.map((m) => (
                <tr key={m.id} className={selected === m.id ? "selected" : undefined}>
                  <td>
                    <button className="model-row" onClick={() => setSelected(m.id)} aria-pressed={selected === m.id}>
                      <strong>{m.name}</strong>
                      <span className="small muted block">{m.publisher} · {ELIGIBILITY_TEXT[m.open_weight_eligibility]}</span>
                    </button>
                  </td>
                  <td className="small" data-label="Type">{m.tasks.map((t) => TASK_LABEL[t] ?? t).join(", ")}</td>
                  <td data-label="Licence"><Pill tone={licenseTone(m.license.status)}>{LICENSE_TEXT[m.license.status]}</Pill></td>
                  <td data-label="State"><Pill tone={m.category === "managed" || m.category === "installed" ? "good" : m.category === "discovered" ? "warn" : "muted"}>{CATEGORY_LABEL[m.category]}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="detail" aria-label="Model details">
          {!current && <p className="muted">Select a model to see its license evidence, files and what it can be used for.</p>}
          {current && <ModelDetail key={current.id} model={current} onChanged={() => load(false)} onNotice={setNotice} />}
        </aside>
      </div>

      {data && (data.scan.problems.length > 0) && (
        <details className="card problems">
          <summary>{data.scan.problems.length} folder or file problem(s) found during the last scan</summary>
          <ul>
            {data.scan.problems.map((p, i) => (
              <li key={i}><strong>{p.kind}</strong> · <code>{p.path}</code> · {p.detail}</li>
            ))}
          </ul>
          <p className="muted small">Nothing was moved or deleted. Broken links are reported only.</p>
        </details>
      )}
    </div>
  );
}

function ModelDetail({ model, onChanged, onNotice }: { model: ModelView; onChanged: () => void; onNotice: (s: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [hashes, setHashes] = useState<Record<string, string>>({});
  const [removeTyped, setRemoveTyped] = useState("");
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [sources, setSources] = useState<{ variant: string; available: boolean; reason: string | null }[]>([]);

  useEffect(() => {
    let alive = true;
    api.model(model.id).then((d) => {
      if (alive) { setWorkflows(d.workflows ?? []); setSources(d.download_sources ?? []); }
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [model.id]);

  useEffect(() => {
    if (!job || (job.state !== "queued" && job.state !== "running")) return;
    const t = setInterval(() => {
      api.download(job.job_id).then((j) => {
        setJob(j);
        if (j.state === "completed") { onNotice(`${model.name} downloaded and recorded as managed.`); setPlan(null); onChanged(); }
      }).catch((e) => setErr(e.message));
    }, 1000);
    return () => clearInterval(t);
  }, [job, model.name, onChanged, onNotice]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setErr(null);
    try { await fn(); }
    catch (e) { setErr(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(null); }
  }

  const confirmFile = (v: VariantView, role: string, path: string) =>
    run("identify", async () => {
      await api.identify(model.id, { variant: v.id, role, path });
      onChanged();
      onNotice("File confirmed. Its identity is recorded until the file changes.");
    });

  const verify = (v: VariantView, role: string, path: string) =>
    run("hash", async () => {
      const r = await api.verifyHash(model.id, { variant: v.id, role, path });
      setHashes((h) => ({ ...h, [path]: `${r.sha256} ${r.from_cache ? "(from cache)" : "(computed now)"}` }));
    });

  const reviewDownload = (v: VariantView) =>
    run("plan", async () => {
      const p = await api.plan(model.id, v.id);
      setPlan(p);
    });

  const startDownload = () => {
    if (!plan) return;
    run("start", async () => {
      const j = await api.startDownload(plan.plan_id);
      setJob(j);
    });
  };

  const removeManaged = (v: VariantView) =>
    run("remove", async () => {
      await api.removeManaged(model.id, v.id, removeTyped);
      setRemoveTyped("");
      onChanged();
      onNotice("Managed files for this variant were removed. External files were not touched.");
    });

  const managedVariant = model.variants.find((v) => v.managed);
  const source = (variant: string) => sources.find((s) => s.variant === variant);

  return (
    <div className="detail-body">
      <h2 className="detail-title">{model.name}</h2>
      <p className="meta">{model.publisher} · <a href={model.source_url} target="_blank" rel="noreferrer">source page</a></p>

      <section aria-labelledby="status-h">
        <h3 id="status-h">Status</h3>
        <dl className="status-list">
          <dt>Found on disk</dt><dd>{model.discovered ? "Yes" : "No"}</dd>
          <dt>Identity confirmed</dt><dd>{model.identified ? "Yes, for at least one variant" : "No"}</dd>
          <dt>Validated</dt><dd>{model.validated ? "Yes" : "No, not validated"}</dd>
          <dt>Smoke-tested</dt><dd>{model.smoke_tested ? "Yes" : "No inference has been run"}</dd>
          <dt>License</dt><dd><Pill tone={licenseTone(model.license.status)}>{LICENSE_TEXT[model.license.status]}</Pill> <code>{model.license.id}</code></dd>
          <dt>Open-weight</dt><dd><Pill tone={eligibilityTone(model.open_weight_eligibility)}>{ELIGIBILITY_TEXT[model.open_weight_eligibility]}</Pill></dd>
        </dl>
      </section>

      <section aria-labelledby="lic-h">
        <h3 id="lic-h">License evidence</h3>
        <p>{model.license.notes}</p>
        {model.license.commercial_restriction && <p className="warn-text">{model.license.commercial_restriction}</p>}
        {model.license.evidence_url && (
          <p className="small">Evidence: <a href={model.license.evidence_url} target="_blank" rel="noreferrer">{model.license.evidence_url}</a>, checked {model.license.verified_on ?? "unknown date"}.</p>
        )}
      </section>

      <section aria-labelledby="wf-h">
        <h3 id="wf-h">Workflows that use this model</h3>
        {workflows.length === 0 ? <p className="muted">None declared.</p> : <ul>{workflows.map((w) => <li key={w}><code>{w}</code></li>)}</ul>}
        {model.limitations.length > 0 && <><h4>Known limitations</h4><ul>{model.limitations.map((l) => <li key={l}>{l}</li>)}</ul></>}
      </section>

      {model.install_guide && (model.category === "missing" || model.category === "discovered") && (
        <section aria-labelledby="install-h">
          <h3 id="install-h">How to install (optional)</h3>
          <p className="small muted">Open Workflow does not download this model. Follow these steps yourself, then scan.{model.approx_size_gb ? ` Repository size about ${model.approx_size_gb} GB.` : ""}</p>
          <pre className="install-guide">{model.install_guide}</pre>
        </section>
      )}

      {model.variants.map((v) => (
        <section key={v.id} className="variant" aria-labelledby={`v-${v.id}`}>
          <h3 id={`v-${v.id}`}>{v.name}</h3>
          <p className="small muted">{v.publisher_files ? "Publisher's own files" : "Not the publisher's files, or unverified"}</p>
          <ul className="components">
            {v.components.map((c) => (
              <li key={c.role}>
                <div className="comp-head">
                  <strong>{c.role.replace("_", " ")}</strong>
                  <Pill tone={c.state === "identified" ? "good" : c.state === "found" ? "warn" : "muted"}>
                    {c.state === "identified" ? "Identity confirmed" : c.state === "found" ? "Found, not confirmed" : "Missing"}
                  </Pill>
                  <Pill tone={c.license_status === "verified" ? "good" : "warn"}>{c.license_status === "verified" ? "License verified" : "License unverified"}</Pill>
                </div>
                {c.identity_stale && <p className="warn-text small">The file changed since you confirmed it. Confirm again.</p>}
                {c.files.length === 0 && <p className="muted small">No file with the expected name was found.</p>}
                {c.files.map((f) => (
                  <div key={f.path} className="file-row">
                    <div>
                      <div className="file-name" title={f.path}>{f.name}</div>
                      <div className="small muted">{bytes(f.size)} · {f.ownership === "external" ? "External (ComfyUI, not managed by Open Workflow)" : f.ownership === "managed" ? "Managed by Open Workflow" : "Shared folder"} · {f.header_ok ? "header ok" : `header: ${f.header_error ?? "not checked"}`}{f.is_symlink ? ` · symbolic link → ${f.link_target}` : ""}</div>
                      <details className="small"><summary>Technical details</summary><code className="path">{f.path}</code>{hashes[f.path] && <div>sha256: <code>{hashes[f.path]}</code></div>}</details>
                    </div>
                    <div className="file-actions">
                      {c.confirmed_path !== f.path && f.header_ok && (
                        <button className="secondary" disabled={!!busy} onClick={() => confirmFile(v, c.role, f.path)}>Confirm this file</button>
                      )}
                      <button className="ghost" disabled={!!busy} onClick={() => verify(v, c.role, f.path)}>Compute checksum</button>
                    </div>
                  </div>
                ))}
              </li>
            ))}
          </ul>
          <div className="variant-actions">
            {source(v.id)?.available ? (
              <button className="secondary" disabled={!!busy || !!job} onClick={() => reviewDownload(v)}>Review download…</button>
            ) : (
              <p className="muted small">Download unavailable: {source(v.id)?.reason ?? "no verified source is recorded."}</p>
            )}
          </div>
        </section>
      ))}

      {plan && (
        <section className="card plan" aria-labelledby="plan-h">
          <h3 id="plan-h">Confirm download</h3>
          <p>Nothing has been downloaded yet. Check this summary, then start.</p>
          <dl className="status-list">
            <dt>Source</dt><dd><a href={plan.source_repo} target="_blank" rel="noreferrer">{plan.source_repo}</a></dd>
            <dt>License</dt><dd>{plan.license.id} ({plan.license.status})</dd>
            <dt>Destination</dt><dd><code className="path">{plan.destination}</code></dd>
            <dt>Total size</dt><dd>{bytes(plan.total_bytes)} (needs {bytes(plan.required_bytes)} free, including a 2 GB margin)</dd>
            <dt>Free space</dt><dd>{bytes(plan.free_bytes)}</dd>
            <dt>Checksums</dt><dd>{plan.checksum_authority === "publisher" ? "Publisher checksum available" : "The publisher provides no checksum; Open Workflow will record a local sha256"}</dd>
            <dt>Needed for</dt><dd>{plan.needed_for_workflows.length ? plan.needed_for_workflows.join(", ") : "No workflow yet"}</dd>
          </dl>
          <ul className="small">{plan.files.map((f) => <li key={f.relpath}><code>{f.relpath}</code> · {bytes(f.size_bytes)}</li>)}</ul>
          <div className="actions">
            <button onClick={startDownload} disabled={!!busy || !!job}>Start download</button>
            <button className="ghost" onClick={() => setPlan(null)}>Cancel</button>
          </div>
        </section>
      )}

      {job && (
        <section className="card" aria-live="polite" aria-labelledby="job-h">
          <h3 id="job-h">Download {job.state}</h3>
          <p className="small muted">{job.current_file ? `Current file: ${job.current_file}` : "Starting…"}</p>
          <p className="small">{job.state === "running" ? "Bytes received so far: " : "Bytes received: "}{bytes(job.bytes_done)} of {bytes(job.bytes_total)}</p>
          {job.error && <p className="warn-text">{job.error}</p>}
          {(job.state === "queued" || job.state === "running") && (
            <button className="secondary" onClick={() => api.cancelDownload(job.job_id).then(setJob)}>Cancel download</button>
          )}
        </section>
      )}

      {managedVariant && (
        <section className="card danger" aria-labelledby="rm-h">
          <h3 id="rm-h">Remove Open Workflow-managed files</h3>
          <p className="small">Only files that Open Workflow downloaded for <strong>{managedVariant.name}</strong> are removed. Files in ComfyUI or shared folders are never deleted here.</p>
          <label className="small">Type <code>{model.id}</code> to confirm
            <input aria-label="Type model id to confirm removal" value={removeTyped} onChange={(e) => setRemoveTyped(e.target.value)} />
          </label>
          <button className="danger-btn" disabled={removeTyped !== model.id || !!busy} onClick={() => removeManaged(managedVariant)}>Remove managed files</button>
        </section>
      )}

      {err && <div className="banner bad" role="alert">{err}</div>}
    </div>
  );
}
