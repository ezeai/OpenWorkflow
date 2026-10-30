import { useEffect, useState } from "react";
import { forgeApi, jobsApi, type ForgeWorkflow, type Hardware, type Health, type WizardState } from "../api";
import { AppCover } from "../AppCover";
import { READINESS, toneFor } from "../Apps";
import { EmptyState, Icon, SectionHeader, StatusBadge, type IconName } from "../ui/primitives";
import { withoutFixtures } from "../ui/release";

const RANK: Record<string, number> = { ready: 0, needs_attention: 1, blocked: 2 };

const STEPS: { icon: IconName; title: string; text: string }[] = [
  { icon: "upload", title: "Import your workflow", text: "Drop in a ComfyUI workflow file or start from an installed template." },
  { icon: "create", title: "Build a friendly app", text: "Open Workflow turns its settings into a form, then checks models and nodes before you run." },
  { icon: "pipelines", title: "Run and connect", text: "Run it on this computer, keep the output and receipt, then chain apps into a pipeline." },
];

const CAPABILITIES: { title: string; text: string }[] = [
  { title: "Workflow-to-app builder", text: "Controls, outputs and requirements are read from the workflow itself, not typed in by hand." },
  { title: "Local generation", text: "Runs go to the ComfyUI on this computer. Cloud is off by default and asks for consent before anything is sent." },
  { title: "Sequential pipelines", text: "Chain apps in order: one app's output image becomes the next app's input." },
  { title: "Execution receipts", text: "Every run saves a receipt of what was actually executed: inputs, seed, output hash and versions." },
];

export function Home({ health, hw, wizard, onSetup, onDiscover, onCreate, onOpenApp, onDetails, onModels }: {
  health: Health | null; hw: Hardware | null; wizard: WizardState | null;
  onSetup: () => void; onDiscover: () => void; onCreate: () => void; onOpenApp: (id: string) => void; onDetails: (id: string) => void; onModels: () => void;
}) {
  const gpu = hw?.gpus[0];
  const [apps, setApps] = useState<ForgeWorkflow[] | null>(null);
  const [shot, setShot] = useState<{ id: string } | null>(null);
  // The hero shows a real output: the newest finished image run on this machine.
  useEffect(() => {
    jobsApi.list().then((r) => { const j = r.jobs.find((x) => x.state === "COMPLETED" && (x as { output_file?: { media?: string } }).output_file?.media === "image"); if (j) setShot({ id: j.id }); }).catch(() => undefined);
  }, []);
  useEffect(() => { forgeApi.list().then((r) => setApps(withoutFixtures(r.workflows))).catch(() => setApps([])); }, []);

  const shown = [...(apps ?? [])]
    .sort((a, b) => (RANK[a.readiness ?? ""] ?? 3) - (RANK[b.readiness ?? ""] ?? 3) || b.imported_at.localeCompare(a.imported_at))
    .slice(0, 4);
  const heroApp = shown[0];
  const engineOk = health?.engine.state === "ok";
  const setupPending = wizard && !wizard.completed.includes("completion") && !wizard.skipped.includes("completion");

  return (
    <div className="page wide home">
      {setupPending && (
        <div className="banner info" role="status">
          Setup is not finished. <button className="link" onClick={onSetup}>Continue setup</button>
        </div>
      )}

      <section className="hero-panel" aria-labelledby="hero-h">
        <div className="hero-copy">
          <p className="eyebrow">Local-first creative apps</p>
          <h1 id="hero-h" className="hero">Your ComfyUI workflows. <span className="hl">Turned into creative apps.</span></h1>
          <p className="lede">Import your workflows, configure friendly controls, run locally, and connect applications into repeatable production pipelines.</p>
          <div className="actions">
            <button className="btn-primary" onClick={onCreate}><Icon name="create" size={16} />Create an App</button>
            <button className="secondary" onClick={onDiscover}><Icon name="discover" size={16} />Explore Apps</button>
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          {shot ? <img className="hero-img" src={forgeApi.runOutputUrl(shot.id)} alt="" /> : heroApp ? <AppCover id={heroApp.id} tone={toneFor(heroApp.name)} name={heroApp.name} /> : <span className="hero-glow" />}
          {shot && <span className="hero-caption">A real output from a local run</span>}
        </div>
      </section>

      <section aria-labelledby="how-h" className="home-section">
        <SectionHeader id="how-h" title="How it works" />
        <ol className="steps-row">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="step-icon"><Icon name={s.icon} size={20} /></span>
              <span className="step-label">Step {i + 1}</span>
              <strong>{s.title}</strong>
              <span className="small muted">{s.text}</span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="cap-h" className="home-section">
        <SectionHeader id="cap-h" title="What you can do today" />
        <ul className="cap-grid">
          {CAPABILITIES.map((c) => (
            <li key={c.title}><strong>{c.title}</strong><span className="small muted">{c.text}</span></li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="apps-h" className="home-section">
        <SectionHeader id="apps-h" title="Your apps" sub="Ready-to-run apps first."
          action={apps && apps.length > 0 ? <button className="link-btn" onClick={onDiscover}>View all <Icon name="arrow" size={14} /></button> : undefined} />
        {apps === null && <div className="card-row four">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton app-skel" aria-busy="true" />)}</div>}
        {apps && apps.length === 0 && (
          <EmptyState title="No apps yet" action={<button className="btn-primary" onClick={onCreate}>Create your first app</button>}>
            Import a ComfyUI workflow you already have, or start from one of ComfyUI's templates.
          </EmptyState>
        )}
        {shown.length > 0 && (
          <ul className="card-row four app-cards">
            {shown.map((w) => {
              const r = w.readiness ? READINESS[w.readiness] : null;
              const ready = w.readiness === "ready";
              return (
                <li key={w.id}>
                  <button className="app-card" onClick={() => (ready ? onOpenApp(w.id) : onDetails(w.id))} aria-label={ready ? `Run ${w.name}` : `Details for ${w.name}`}>
                    <AppCover id={w.id} tone={toneFor(w.name)} name={w.name} />
                    <span className="app-card-body">
                      <span className="app-card-name">{w.name}</span>
                      <span className="small muted">{w.origin.startsWith("comfyui-template:") ? "From a ComfyUI template" : "Imported workflow"}</span>
                      <span className="app-card-foot">
                        <StatusBadge tone={(r?.cls as "good" | "warn" | "bad") ?? "neutral"}>{r?.text ?? "Unknown"}</StatusBadge>
                        <span className="app-card-cta">{ready ? "Run" : "Details"}</span>
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="sys-h" className="home-section sys-strip">
        <SectionHeader id="sys-h" title="System readiness" action={<button className="link-btn" onClick={onModels}>Model library <Icon name="arrow" size={14} /></button>} />
        <dl className="sys-row">
          <div><dt>ComfyUI</dt><dd>{!health ? <span className="muted">Checking…</span> : <StatusBadge tone={engineOk ? "good" : "warn"}>{engineOk ? "Connected" : "Not running"}</StatusBadge>}</dd></div>
          <div><dt>GPU</dt><dd>{!hw ? <span className="muted">Checking…</span> : gpu ? `${gpu.name} · ${(gpu.vram_total_mb / 1024).toFixed(0)} GB` : <span className="muted">None detected</span>}</dd></div>
          <div><dt>Memory</dt><dd>{hw ? `${hw.ram_total_gb} GB` : <span className="muted">Checking…</span>}</dd></div>
        </dl>
        {health && !engineOk && <p className="hint small">Start ComfyUI yourself; this updates on its own.</p>}
      </section>
    </div>
  );
}
