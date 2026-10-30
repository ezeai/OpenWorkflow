import { useEffect, useState } from "react";
import { forgeApi, type ForgeWorkflow } from "../api";
import { AppCover } from "../AppCover";
import { ImportPanel, READINESS, toneFor } from "../Apps";
import { Tabs } from "../Tabs";
import { EmptyState, PageHeader, StatusBadge, Stepper } from "../ui/primitives";
import { withoutFixtures } from "../ui/release";

export const CREATE_STEPS = [
  { title: "Workflow", sub: "Import or pick a workflow" },
  { title: "Configure", sub: "Name it, choose its controls" },
  { title: "Preview", sub: "Test and check what it needs" },
  { title: "Publish", sub: "Save it to your library" },
];

/**
 * Step 1 of making an app. Choosing or importing a workflow stores it through the real Forge API, then hands
 * over to the app's own workspace, where Configure (Edit app), Preview (Use and Analysis) and saving already live.
 */
export function CreateApp({ onConfigure }: { onConfigure: (id: string) => void }) {
  const [source, setSource] = useState<"new" | "mine">("new");
  const [mine, setMine] = useState<ForgeWorkflow[] | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { forgeApi.list().then((r) => setMine(withoutFixtures(r.workflows))).catch(() => setMine([])); }, []);
  const shown = (mine ?? []).filter((w) => w.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div className="page wide create-app">
      <PageHeader eyebrow="Create" title="Create an app" lede="Turn a ComfyUI workflow into an app anyone can use: a few clear controls, a Generate button, and a check of what it needs." />
      <div className="create-layout">
        <aside className="create-steps"><Stepper steps={CREATE_STEPS} current={0} /></aside>
        <section className="create-main" aria-labelledby="choose-h">
          <h2 id="choose-h" className="section-title">1. Choose a workflow</h2>
          <Tabs label="Workflow source" value={source} onChange={setSource}
            items={[{ id: "new", label: "Import or template", panelId: "create-panel" }, { id: "mine", label: `My workflows${mine ? ` (${mine.length})` : ""}`, panelId: "create-panel" }]} />
          <div id="create-panel" role="tabpanel" aria-labelledby={`tab-${source}`} className="create-panel">
            {source === "new" && <ImportPanel onImported={(w) => onConfigure(w.id)} />}
            {source === "mine" && (<>
              <input type="search" className="search-input" aria-label="Search your workflows" placeholder="Search workflows" value={q} onChange={(e) => setQ(e.target.value)} />
              {mine === null && <div className="skeleton" aria-busy="true" style={{ height: 160 }} />}
              {mine && mine.length === 0 && <EmptyState title="No workflows imported yet">Import a workflow file or pick a template first.</EmptyState>}
              {mine && mine.length > 0 && shown.length === 0 && <p className="muted" role="status">No workflow matches "{q}".</p>}
              <ul className="pick-grid">
                {shown.map((w) => {
                  const r = w.readiness ? READINESS[w.readiness] : null;
                  return (
                    <li key={w.id} className="pick-card">
                      <AppCover id={w.id} tone={toneFor(w.name)} name={w.name} />
                      <strong className="ellipsis">{w.name}</strong>
                      <StatusBadge tone={(r?.cls as "good" | "warn" | "bad") ?? "neutral"}>{r?.text ?? "Unknown"}</StatusBadge>
                      <button className="secondary" onClick={() => onConfigure(w.id)} aria-label={`Configure ${w.name}`}>Select</button>
                    </li>
                  );
                })}
              </ul>
            </>)}
          </div>
        </section>
      </div>
    </div>
  );
}
