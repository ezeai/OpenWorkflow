import { useCallback, useEffect, useState } from "react";
import { api, forgeApi, type Hardware, type Health } from "./api";
import { Tabs } from "./Tabs";
import { Icon, PageHeader, SectionHeader, StatusBadge } from "./ui/primitives";

type Root = { path: string; ownership: string; label: string };
type Section = "general" | "models" | "hardware" | "storage" | "providers";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "general", label: "General" },
  { id: "models", label: "Models" },
  { id: "hardware", label: "Hardware" },
  { id: "storage", label: "Storage" },
  { id: "providers", label: "Providers" },
];

const OWNERSHIP: Record<string, string> = { external: "ComfyUI (external)", managed: "Open Workflow managed", user_shared: "Shared folder" };

export function Settings({ onRerunSetup, health }: { onRerunSetup: () => void; health: Health | null }) {
  const [section, setSection] = useState<Section>("general");
  const [roots, setRoots] = useState<Root[] | null>(null);
  const [newPath, setNewPath] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [found, setFound] = useState<{ path: string; models_dir: string }[] | null>(null);
  const [hw, setHw] = useState<Hardware | null>(null);
  const [scan, setScan] = useState<{ files_found: number; problems: unknown[] } | null>(null);
  const [providers, setProviders] = useState<{ local: { state: string; message: string }; cloud: { state: string; message: string }; default: string } | null>(null);

  const load = useCallback(() => api.roots().then((r) => setRoots(r.roots)).catch((e: Error) => setErr(e.message)), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    api.hardware().then(setHw).catch(() => setHw(null));
    forgeApi.providers().then(setProviders).catch(() => setProviders(null));
  }, []);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    }
  };

  const runScan = async () => {
    setErr(null); setMsg(null);
    try { setScan(await api.scan()); } catch (e) { setErr(e instanceof Error ? e.message : "The scan failed."); }
  };

  const engineOk = health?.engine.state === "ok";
  const gpu = hw?.gpus[0];

  return (
    <div className="page wide settings">
      <PageHeader eyebrow="System" title="Settings" lede="Saved on this machine. Nothing here changes a model file." />
      <Tabs label="Settings sections" value={section} onChange={setSection}
        items={SECTIONS.map((s) => ({ id: s.id, label: s.label, panelId: "settings-panel" }))} />
      <div id="settings-panel" role="tabpanel" aria-labelledby={`tab-${section}`} className="settings-panel">

        {section === "general" && (<>
          <section className="card" aria-labelledby="setup-h">
            <SectionHeader id="setup-h" title="Setup" sub="Run the first-run checks again: ComfyUI, models and folders. Your projects are not touched." />
            <button className="secondary" onClick={() => api.wizardReset().then(onRerunSetup).catch((e: Error) => setErr(e.message))}>Rerun setup</button>
          </section>
          <section className="card" aria-labelledby="diag-h">
            <SectionHeader id="diag-h" title="Diagnostics" sub="A zip with versions, hardware, recent job states and the log tail with secrets removed. Nothing is sent anywhere; you attach it to a bug report yourself." />
            <a className="secondary" href="/api/diagnostics">Download diagnostics</a>
          </section>
        </>)}

        {section === "models" && (<>
          <section className="card" aria-labelledby="scan-h">
            <SectionHeader id="scan-h" title="Model files" sub="Scan the model folders listed under Storage. Installed files are found by name and size; a file is only treated as ready once it is checked." />
            <div className="actions">
              <button className="secondary" onClick={() => void runScan()}>Scan model folders</button>
            </div>
            {scan && <p className="small" role="status">{scan.files_found} model file{scan.files_found === 1 ? "" : "s"} found{scan.problems.length ? `, ${scan.problems.length} problem${scan.problems.length === 1 ? "" : "s"} to review in the Model Library` : ""}.</p>}
          </section>
        </>)}

        {section === "hardware" && (<>
          <section className="card" aria-labelledby="hw-h">
            <SectionHeader id="hw-h" title="This machine" sub="Read from the computer when this page opens. Nothing is estimated." />
            {!hw ? <div className="skeleton" aria-busy="true" style={{ height: 120 }} /> : (
              <dl className="readiness-list">
                <div><dt>GPU</dt><dd>{gpu ? <><span>{gpu.name}</span><span className="meta">{(gpu.vram_free_mb / 1024).toFixed(1)} of {(gpu.vram_total_mb / 1024).toFixed(0)} GB VRAM free</span></> : <span className="muted">No NVIDIA GPU detected</span>}</dd></div>
                <div><dt>System memory</dt><dd>{hw.ram_available_gb} of {hw.ram_total_gb} GB available</dd></div>
                <div><dt>CPU</dt><dd>{hw.cpu_threads} threads</dd></div>
                <div><dt>System</dt><dd>{hw.os}<span className="meta">Python {hw.python}</span></dd></div>
                <div><dt>FFmpeg</dt><dd>{hw.ffmpeg.available ? <StatusBadge tone="good">Found</StatusBadge> : <><StatusBadge tone="warn">Not found</StatusBadge><span className="small muted">Video import and compare are limited.</span></>}</dd></div>
              </dl>
            )}
          </section>
          <section className="card" aria-labelledby="engine-h">
            <SectionHeader id="engine-h" title="ComfyUI connection" />
            {!health ? <div className="skeleton" aria-busy="true" style={{ height: 60 }} /> : (
              <dl className="readiness-list">
                <div><dt>State</dt><dd><StatusBadge tone={engineOk ? "good" : "warn"}>{engineOk ? "Connected" : "Not running"}</StatusBadge></dd></div>
                <div><dt>Address</dt><dd><code>{health.engine.url}</code></dd></div>
              </dl>
            )}
            {health && !engineOk && <p className="small muted">Start ComfyUI yourself. Open Workflow does not start it.</p>}
          </section>
        </>)}

        {section === "storage" && (<>
          <section className="card" aria-labelledby="folders-h">
            <SectionHeader id="folders-h" title="Model folders" sub="Open Workflow looks in these folders. Adding or removing one changes only this list. No model file is moved, copied, renamed or deleted." />
            {roots === null && !err && <div className="skeleton" aria-busy="true" style={{ height: 60 }} />}
            {roots && (
              <ul className="roots">
                {roots.map((r) => (
                  <li key={r.path}>
                    <div>
                      <strong>{r.label}</strong> <StatusBadge tone="neutral">{OWNERSHIP[r.ownership] ?? "Folder"}</StatusBadge>
                      <div className="small muted"><code>{r.path}</code></div>
                    </div>
                    {r.ownership === "user_shared" && (
                      <button className="ghost" onClick={() => run(() => api.removeRoot(r.path), "Folder removed from the list. Files were not touched.")}>Remove from list</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); if (newPath.trim()) run(() => api.addRoot(newPath.trim()).then(() => setNewPath("")), "Folder added. It will be scanned."); }}>
              <label className="small">Add a folder
                <input aria-label="Folder path to add" placeholder="e.g. D:\models" value={newPath} onChange={(e) => setNewPath(e.target.value)} />
              </label>
              <button type="submit" className="secondary" disabled={!newPath.trim()}>Add folder</button>
            </form>
          </section>
          <section className="card" aria-labelledby="comfy-h">
            <SectionHeader id="comfy-h" title="ComfyUI installation" sub="Searches common locations without changing anything. Choosing a folder only tells Open Workflow where ComfyUI is. ComfyUI's own configuration is never modified." />
            <div className="actions">
              <button className="secondary" onClick={() => api.detectComfy().then((r) => setFound(r.found)).catch((e: Error) => setErr(e.message))}>Look for ComfyUI</button>
            </div>
            {found && found.length === 0 && <p className="muted">No ComfyUI installation was found in the usual places. You can add its models folder above.</p>}
            {found && found.length > 0 && (
              <ul className="roots">
                {found.map((f) => (
                  <li key={f.path}>
                    <div><code>{f.path}</code><div className="small muted">models: <code>{f.models_dir}</code></div></div>
                    <button className="secondary" onClick={() => run(() => api.setComfyRoot(f.path), "ComfyUI folder saved.")}>Use this</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>)}

        {section === "providers" && (<>
          <section className="card" aria-labelledby="local-h">
            <SectionHeader id="local-h" title="Local ComfyUI" sub="The default. Workflows, images and files stay on this computer." />
            {!providers ? <div className="skeleton" aria-busy="true" style={{ height: 60 }} /> : (
              <p className="small"><StatusBadge tone={providers.local.state === "ok" ? "good" : "warn"}>{providers.local.state === "ok" ? "Available" : "Not running"}</StatusBadge> {providers.local.message}</p>
            )}
          </section>
          <section className="card" aria-labelledby="cloud-h">
            <SectionHeader id="cloud-h" title="External providers" sub="Optional. Nothing is sent to an external provider unless you choose it and confirm the exact files first." />
            {!providers ? <div className="skeleton" aria-busy="true" style={{ height: 60 }} /> : (
              <p className="small"><StatusBadge tone="neutral">{providers.cloud.state === "ok" ? "Configured" : "Off"}</StatusBadge> {providers.cloud.message}</p>
            )}
            <p className="small muted"><Icon name="alert" size={14} /> Provider security behaviour is unchanged by this page.</p>
          </section>
        </>)}
      </div>

      {msg && <div className="banner info" role="status">{msg}</div>}
      {err && <div className="banner bad" role="alert">{err}</div>}
    </div>
  );
}
