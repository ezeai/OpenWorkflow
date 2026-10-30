import { useEffect, useState } from "react";
import { forgeApi, type ForgeWorkflow } from "../api";
import { Chain } from "../Chain";
import { Flow } from "../Flow";
import { Tabs } from "../Tabs";
import { EmptyState, PageHeader } from "../ui/primitives";
import { withoutFixtures } from "../ui/release";

/** Pipelines run apps one after another; each step's output image becomes the next step's input. Sequential only. */
export function Pipelines({ onCreate }: { onCreate: () => void }) {
  const [apps, setApps] = useState<ForgeWorkflow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mode, setMode] = useState<"flow" | "chain">("flow");

  useEffect(() => { forgeApi.list().then((r) => setApps(withoutFixtures(r.workflows))).catch((e) => setErr(e instanceof Error ? e.message : "Could not load apps.")); }, []);

  return (
    <div className="page wide pipelines">
      <PageHeader eyebrow="Production" title="Pipelines"
        lede="Chain apps into a production run. Steps run in order on this computer; the image one step makes is handed to the next as its input." />
      <ol className="seq-strip" aria-label="How a pipeline runs">
        <li><span className="seq-n">1</span><strong>Application 1</strong><span className="small muted">Runs first and makes an image.</span></li>
        <li className="seq-arrow" aria-hidden="true">→</li>
        <li><span className="seq-n">2</span><strong>Application 2</strong><span className="small muted">Receives that image as its input.</span></li>
        <li className="seq-arrow" aria-hidden="true">→</li>
        <li><span className="seq-n">3</span><strong>Final output</strong><span className="small muted">Saved with a receipt for each step.</span></li>
      </ol>
      <p className="small muted limits-note">Steps run one after another on this computer. Branching, parallel steps, cancelling a whole run and resuming after a restart are not supported yet. Images only.</p>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {apps === null && !err && <div className="skeleton" aria-busy="true" style={{ height: 220 }} />}
      {apps && apps.length === 0 && (
        <EmptyState icon="pipelines" title="A pipeline needs apps" action={<button className="btn-primary" onClick={onCreate}>Create an app</button>}>
          Create or import at least one app, then come back to connect apps into steps.
        </EmptyState>
      )}
      {apps && apps.length > 0 && (<>
        <Tabs label="Pipeline type" value={mode} onChange={setMode}
          items={[{ id: "flow", label: "Multi-step flow", panelId: "pipe-panel" }, { id: "chain", label: "Chain two apps", panelId: "pipe-panel" }]} />
        <div id="pipe-panel" role="tabpanel" aria-labelledby={`tab-${mode}`} className="pipe-panel">
          {mode === "flow" ? <Flow key="flow" apps={apps} /> : <Chain key="chain" apps={apps} />}
        </div>
      </>)}
    </div>
  );
}
