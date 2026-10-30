import { useEffect, useRef, useState } from "react";
import { api, forgeApi, navTabsApi, projectsApi, type Health, type NavTab, type WizardState } from "./api";
import { ModelLibrary } from "./ModelLibrary";
import { Settings } from "./Settings";
import { Wizard } from "./Wizard";
import { ProjectsPage, ProjectWorkspace } from "./Projects";
import { Director } from "./Director";
import { Jobs } from "./Jobs";
import { Canvas } from "./Canvas";
import { Apps } from "./Apps";
import { Influencer } from "./Influencer";
import { Home } from "./pages/Home";
import { CreateApp } from "./pages/CreateApp";
import { Pipelines } from "./pages/Pipelines";
import { AppDetails } from "./pages/AppDetails";
import { Icon, type IconName } from "./ui/primitives";
import { SUBMISSION_MODE, withoutFixtures } from "./ui/release";

type View = "home" | "apps" | "create" | "pipelines" | "posters" | "projects" | "characters" | "worlds" | "influencer" | "director" | "canvas" | "models" | "jobs" | "settings" | "setup" | "details";

const NAV: { id: View; label: string; icon: IconName }[] = [
  { id: "home", label: "Home", icon: "home" },
  { id: "apps", label: "Discover", icon: "discover" },
  { id: "create", label: "Create", icon: "create" },
  { id: "pipelines", label: "Pipelines", icon: "pipelines" },
  { id: "projects", label: "My Projects", icon: "projects" },
  { id: "characters", label: "Characters", icon: "character" },
  { id: "worlds", label: "Worlds", icon: "world" },
  { id: "director", label: "Director", icon: "director" },
  { id: "canvas", label: "Canvas", icon: "canvas" },
  { id: "influencer", label: "AI Influencer", icon: "character" },
  { id: "models", label: "Model Library", icon: "library" },
  { id: "jobs", label: "Run History", icon: "history" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const FULL_GROUPS: { title: string; ids: View[] }[] = [
  { title: "Workspace", ids: ["home", "apps", "create"] },
  { title: "Production", ids: ["pipelines", "projects", "director", "canvas", "characters", "worlds"] },
  { title: "Library", ids: ["models", "jobs"] },
  { title: "System", ids: ["settings"] },
];

// Submission build: only what is verified end to end. Hidden views keep their routes, code and data (see ui/release.ts).
const SUBMISSION_GROUPS: { title: string; ids: View[] }[] = [
  { title: "Workspace", ids: ["home", "apps", "create"] },
  { title: "Production", ids: ["pipelines"] },
  { title: "Library", ids: ["jobs", "models"] },
  { title: "System", ids: ["settings"] },
];

const GROUPS = SUBMISSION_MODE ? SUBMISSION_GROUPS : FULL_GROUPS;

function usePoll<T>(fn: () => Promise<T>, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      fn().then((d) => alive && (setData(d), setError(null))).catch((e: Error) => alive && setError(e.message));
    tick();
    const t = setInterval(tick, ms);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [fn, ms]);
  return { data, error };
}

function StatusPill({ health, error }: { health: Health | null; error: string | null }) {
  if (error) return <span className="status-badge bad" role="status"><span className="status-dot" aria-hidden="true" />Backend offline</span>;
  if (!health) return <span className="status-badge neutral" role="status"><span className="status-dot" aria-hidden="true" />Connecting…</span>;
  const ok = health.engine.state === "ok";
  return <span className={`status-badge ${ok ? "good" : "warn"}`} role="status"><span className="status-dot" aria-hidden="true" />{ok ? "ComfyUI connected" : "ComfyUI not running"}</span>;
}

type Hit = { kind: "App" | "Project"; id: string; label: string };

/** Searches the apps and projects the backend actually has. Loaded on first focus, refreshed on each focus. */
function GlobalSearch({ onPick }: { onPick: (h: Hit) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Hit[] | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const load = () => {
    Promise.all([forgeApi.list().catch(() => ({ workflows: [] })), SUBMISSION_MODE ? Promise.resolve({ projects: [] }) : projectsApi.list().catch(() => ({ projects: [] }))]).then(([a, p]) =>
      setItems([
        ...withoutFixtures(a.workflows).map((w) => ({ kind: "App" as const, id: w.id, label: w.name })),
        ...p.projects.filter((x) => x.readable).map((x) => ({ kind: "Project" as const, id: x.id, label: x.title })),
      ]));
  };
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const term = q.trim().toLowerCase();
  const hits = term ? (items ?? []).filter((h) => h.label.toLowerCase().includes(term)).slice(0, 8) : [];
  const pick = (h: Hit) => { onPick(h); setQ(""); setOpen(false); };

  return (
    <div className="global-search" ref={box}>
      <Icon name="discover" size={16} className="search-icon" />
      <input type="search" role="combobox" aria-expanded={open && term.length > 0} aria-controls="search-results" aria-autocomplete="list"
        aria-label="Search apps" placeholder="Search apps" value={q}
        onFocus={() => { load(); setOpen(true); }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && hits[active]) { e.preventDefault(); pick(hits[active]); }
          else if (e.key === "Escape") setOpen(false);
        }} />
      {open && term && (
        <ul id="search-results" role="listbox" className="search-results" aria-label="Search results">
          {items === null && <li className="muted small">Searching…</li>}
          {items && hits.length === 0 && <li className="muted small">Nothing matches "{q}".</li>}
          {hits.map((h, i) => (
            <li key={`${h.kind}-${h.id}`} role="option" aria-selected={i === active} className={i === active ? "on" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(h); }} onMouseEnter={() => setActive(i)}>
              <Icon name={h.kind === "App" ? "create" : "projects"} size={16} />
              <span className="ellipsis">{h.label}</span><span className="search-kind">{h.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NeedsProject({ what, onGo }: { what: string; onGo: () => void }) {
  return (
    <div className="page">
      <div className="empty-state" role="status">
        <span className="empty-icon"><Icon name="projects" size={22} /></span>
        <h3>Open a project first</h3>
        <p className="muted">{what} live inside a project.</p>
        <button className="btn-primary" onClick={onGo}>Go to My Projects</button>
      </div>
    </div>
  );
}

export function App() {
  const [view, setView] = useState<View>("home");
  const { data: health, error } = usePoll(api.health, 5000);
  const { data: hw } = usePoll(api.hardware, 15000);
  const gpu = hw?.gpus[0];
  const [wizard, setWizard] = useState<WizardState | null>(null);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [wizardLoaded, setWizardLoaded] = useState(false);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    api.wizard().then(setWizard).catch(() => setWizard(null)).finally(() => setWizardLoaded(true));
  }, []);

  const [tabs, setTabs] = useState<NavTab[]>([]);
  const [openApp, setOpenApp] = useState<{ id: string; tab: "use" | "build" } | null>(null);
  const [currentAppId, setCurrentAppId] = useState<string | null>(null);
  const [pinMsg, setPinMsg] = useState<string | null>(null);

  const go = (v: View) => { setView(v); setDrawer(false); if (v !== "apps") setCurrentAppId(null); document.getElementById("main")?.focus(); };
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const openDetails = (id: string) => { setDetailsId(id); go("details"); };
  const openAppPage = (id: string, tab: "use" | "build" = "use") => { setOpenApp({ id, tab }); go("apps"); };

  const unpin = async (appId: string, label: string) => {
    setPinMsg(null);
    try {
      const r = await navTabsApi.save(tabs.filter((t) => t.app_id !== appId));
      setTabs(r.tabs);
      if (currentAppId === appId) setCurrentAppId(null);
    } catch (e) { setPinMsg(`Could not remove "${label}" from the nav.`); }
  };
  useEffect(() => {
    navTabsApi.list().then((r) => setTabs(r?.tabs ?? [])).catch(() => setTabs([]));
  }, []);
  useEffect(() => {
    if (!drawer) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawer(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [drawer]);

  const current = NAV.find((n) => n.id === view) ?? { label: view === "details" ? "App details" : "Setup" };
  const group = view === "details" ? "Workspace" : GROUPS.find((g) => g.ids.includes(view))?.title;

  return (
    <div className={`shell${drawer ? " drawer-open" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <nav aria-label="Main" className="sidebar" id="sidebar">
        <div className="brand"><span className="brand-mark" aria-hidden="true" />Open Workflow</div>
        <button className="nav-cta" onClick={() => go("create")}><Icon name="create" size={16} />New app</button>
        {tabs.length > 0 && (
          <div className="nav-group">
            <p className="nav-group-title" aria-hidden="true">Pinned</p>
            {tabs.map((t) => {
              const active = view === "apps" && currentAppId === t.app_id;
              return (
                <div key={t.app_id} className="nav-pinned-row">
                  <button className={active ? "nav active" : "nav"} aria-current={active ? "page" : undefined}
                    title={`Open ${t.label}`} onClick={() => openAppPage(t.app_id)}>
                    <span className="nav-label">{t.label}</span>
                  </button>
                  <button className="nav-remove" aria-label={`Remove ${t.label} from the nav`} title="Remove from the nav"
                    onClick={() => void unpin(t.app_id, t.label)}>×</button>
                </div>
              );
            })}
            {pinMsg && <p className="muted small" role="alert">{pinMsg}</p>}
          </div>
        )}
        {GROUPS.map((g) => (
          <div key={g.title} className="nav-group">
            <p className="nav-group-title" aria-hidden="true">{g.title}</p>
            {NAV.filter((n) => g.ids.includes(n.id)).map((n) => {
              const on = n.id === view && !(view === "apps" && currentAppId && tabs.some((t) => t.app_id === currentAppId));
              return (
                <button key={n.id} className={on ? "nav active" : "nav"} aria-current={on ? "page" : undefined} onClick={() => go(n.id)}>
                  <span className="nav-label"><Icon name={n.icon} className="nav-icon" />{n.label}</span>
                </button>
              );
            })}
          </div>
        ))}
        <div className="nav-spacer" />
        {gpu && (
          <div className="gpu-card">
            <div className="row"><span className="ellipsis">{gpu.name}</span></div>
            <div className="meter" role="img" aria-label={`VRAM in use: ${((gpu.vram_total_mb - gpu.vram_free_mb) / 1024).toFixed(1)} of ${(gpu.vram_total_mb / 1024).toFixed(0)} GB`}>
              <span style={{ width: `${Math.round((1 - gpu.vram_free_mb / gpu.vram_total_mb) * 100)}%` }} />
            </div>
            <div className="stats"><span>VRAM {((gpu.vram_total_mb - gpu.vram_free_mb) / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} GB</span></div>
          </div>
        )}
      </nav>
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} aria-hidden="true" />}
      <main id="main" tabIndex={-1}>
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label={drawer ? "Close navigation" : "Open navigation"} aria-expanded={drawer} aria-controls="sidebar"
            onClick={() => setDrawer((d) => !d)}><Icon name={drawer ? "close" : "menu"} /></button>
          <span className="crumb" aria-live="polite">{group && <span className="crumb-group">{group} / </span>}<span className="crumb-page">{current.label}</span></span>
          <GlobalSearch onPick={(h) => (h.kind === "App" ? openAppPage(h.id) : (setActiveProject(h.id), go("characters")))} />
          <StatusPill health={health} error={error} />
        </header>
        {view === "home" && <Home health={health} hw={hw} wizard={wizard}
          onSetup={() => go("setup")} onDiscover={() => go("apps")} onCreate={() => go("create")} onOpenApp={(id) => openAppPage(id)} onDetails={openDetails}
          onModels={() => go("models")} />}
        {view === "create" && <CreateApp onConfigure={(id) => openAppPage(id, "build")} />}
        {view === "details" && detailsId && <AppDetails key={detailsId} id={detailsId} onBack={() => go("apps")} onRun={() => openAppPage(detailsId)} onConfigure={() => openAppPage(detailsId, "build")} />}
        {view === "pipelines" && <Pipelines onCreate={() => go("create")} />}
        {view === "projects" && <ProjectsPage onOpen={(id) => { setActiveProject(id); go("characters"); }} />}
        {view === "influencer" && <Influencer />}
        {(view === "characters" || view === "worlds") && (activeProject ? (
          <ProjectWorkspace key={`${activeProject}-${view}`} projectId={activeProject} initialKind={view === "characters" ? "character" : "world"} onBack={() => go("projects")} />
        ) : <NeedsProject what="Characters and worlds" onGo={() => go("projects")} />)}
        {view === "models" && <ModelLibrary />}
        {view === "settings" && <Settings health={health} onRerunSetup={() => { setWizard(null); go("setup"); api.wizard().then(setWizard).catch(() => undefined); }} />}
        {view === "setup" && wizardLoaded && (
          <Wizard onDone={() => { api.wizard().then(setWizard).catch(() => undefined); go("home"); }} />
        )}
        {view === "jobs" && <Jobs />}
        {view === "apps" && <Apps openId={openApp?.id ?? null} openTab={openApp?.tab} onOpened={() => setOpenApp(null)} onOpenChange={setCurrentAppId} onDetails={openDetails} tabs={tabs} onTabsChanged={setTabs} />}
        {view === "canvas" && (activeProject ? <Canvas key={activeProject} projectId={activeProject} /> : <NeedsProject what="Canvas pipelines" onGo={() => go("projects")} />)}
        {view === "director" && (activeProject ? <Director key={activeProject} projectId={activeProject} /> : <NeedsProject what="Shots" onGo={() => go("projects")} />)}
      </main>
    </div>
  );
}
