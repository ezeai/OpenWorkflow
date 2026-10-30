import { useEffect, useRef, useState } from "react";
import { forgeApi, forgeCharacterApi, forgeShotApi, jobsApi, type Character, type DatasetItem, type ForgeWorkflow, type JobProgress, type ShotPreset, type SpecComponent } from "./api";
import { Creator } from "./Creator";

// AI Influencer: a character is a name, an anchor photo and a history of identity checks. Scenes and the character
// sheet are imported apps, run with the anchor as their image input. Nothing trains: the same reference goes in every time.

export interface SceneControls { image: string | null; prompt: string | null; seed: string | null }

// Which controls of an app receive the anchor, the prompt and the seed. Pure, so it can be tested.
export function sceneControls(components: SpecComponent[]): SceneControls {
  const main = components.filter((c) => c.group === "main" && !c.hidden);
  const image = main.find((c) => c.type === "image_upload")?.id ?? null;
  const prompts = main.filter((c) => c.type === "prompt" && c.binding.input !== "negative_prompt");
  const prompt = (prompts.find((c) => !/negative/i.test(c.label)) ?? prompts[0])?.id ?? null;
  const seed = main.find((c) => c.type === "seed")?.id ?? null;
  return { image, prompt, seed };
}

// Which stage of the render a ComfyUI node belongs to, in the order a render goes through them.
export const STAGES = ["Queued", "Loading models", "Encoding prompt", "Sampling", "Decoding", "Saving"] as const;
export type Stage = (typeof STAGES)[number];
export function stageOf(state: string | null, node: string | null | undefined): Stage {
  if (!state || state === "QUEUED" || state === "VALIDATING") return "Queued";
  if (state === "POSTPROCESSING") return "Saving";
  const n = (node ?? "").toLowerCase();
  if (!n || n.startsWith("load") || n.includes("loader") || n.includes("vae") && !n.includes("decode")) return "Loading models";
  if (n.includes("text") || n.includes("clip") || n.includes("prompt") || n.includes("guidance") || n.includes("encode")) return "Encoding prompt";
  if (n.includes("sampler") || n.includes("sampling") || n.includes("guider") || n.includes("scheduler")) return "Sampling";
  if (n.includes("decode") || n.includes("vae")) return "Decoding";
  if (n.includes("save") || n.includes("preview")) return "Saving";
  return "Sampling";
}

export function percentOf(p: JobProgress | null | undefined): number | null {
  if (!p || p.mode !== "determinate" || !p.steps) return null;
  return Math.min(100, Math.round(((p.step ?? 0) / p.steps) * 100));
}

const JOB_DONE = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"]);
const SHEET_PROMPT = "Character sheet of the same person as the reference: a close-up head shot with the full wavy hair visible, then a full-body front view cropped at the neck so the head is not shown, then side and back views with the same outfit, plain light background";
type Tab = "sheet" | "shots" | "dataset" | "scenes" | "create" | "checks";
export interface Render { jobId: string; kind: "sheet" | "scene" | "shot"; preset?: string | null; inDataset?: boolean; title: string; state: string; seconds: number; progress: JobProgress | null; score?: number | null; checked?: boolean; faceFound?: boolean; passed?: boolean }

// Watches a job until it ends, reporting each change: state, seconds run and ComfyUI's progress record.
async function watch(jobId: string, onUpdate: (state: string, seconds: number, progress: JobProgress | null) => void): Promise<string> {
  const started = Date.now();
  for (let i = 0; i < 1200; i++) {
    const j = await jobsApi.get(jobId);
    onUpdate(j.state, Math.round((Date.now() - started) / 1000), j.progress ?? null);
    if (JOB_DONE.has(j.state)) return j.state;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return "RUNNING";
}

export function ProgressPanel({ state, seconds, progress }: { state: string; seconds: number; progress: JobProgress | null }) {
  const pct = percentOf(progress);
  const stage = stageOf(state, progress?.node);
  const done = JOB_DONE.has(state);
  const current = STAGES.indexOf(stage);
  return (
    <div className="infl-progress-panel" role="status" aria-live="polite">
      <div className="infl-progress-top">
        <strong>{done ? (state === "COMPLETED" ? "Done" : state.toLowerCase()) : stage}</strong>
        <span className="muted">
          {pct !== null ? `${progress?.step ?? 0} / ${progress?.steps} steps · ` : ""}
          {progress?.nodes_finished ? `${progress.nodes_finished} nodes · ` : ""}
          {seconds} s
        </span>
      </div>
      <div className="infl-bar" aria-label={pct !== null ? `${pct}% done` : "working"}>
        <div className={`infl-bar-fill${pct === null && !done ? " indeterminate" : ""}`} style={{ width: done ? "100%" : pct !== null ? `${pct}%` : undefined }} />
      </div>
      <ol className="infl-stages">
        {STAGES.map((s, i) => (
          <li key={s} className={i < current || (done && state === "COMPLETED") ? "past" : i === current && !done ? "now" : ""}>{s}</li>
        ))}
      </ol>
      {!done && state === "RUNNING" && seconds > 60 && <p className="muted">A first render after a restart takes longer while models load.</p>}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="infl-hint">{children}</p>;
}

export function Influencer() {
  const [apps, setApps] = useState<ForgeWorkflow[]>([]);
  const [list, setList] = useState<{ id: string; name: string; anchor: boolean; checks: number }[] | null>(null);
  const [cid, setCid] = useState<string | null>(null);
  const [character, setCharacter] = useState<Character | null>(null);
  const [newName, setNewName] = useState("");
  const [newNotes, setNewNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("sheet");
  const [renders, setRenders] = useState<Render[]>([]);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  // scenes
  const [appId, setAppId] = useState("");
  const [controls, setControls] = useState<SceneControls | null>(null);
  const [scenePrompt, setScenePrompt] = useState("");
  const [seed, setSeed] = useState(42);
  const [checking, setChecking] = useState<string | null>(null);

  // character sheet
  const [sheetAppId, setSheetAppId] = useState("");
  const [sheetControls, setSheetControls] = useState<SceneControls | null>(null);
  const [sheetPrompt, setSheetPrompt] = useState(SHEET_PROMPT);

  // shots and dataset
  const [presets, setPresets] = useState<ShotPreset[]>([]);
  const [picked, setPicked] = useState<string[]>(["close_up", "medium", "full_body"]);
  const [dataset, setDataset] = useState<DatasetItem[]>([]);
  useEffect(() => { forgeShotApi.presets().then((r) => setPresets(r.presets)).catch(() => setPresets([])); }, []);
  useEffect(() => {
    if (!cid) { setDataset([]); return; }
    forgeShotApi.dataset(cid).then((r) => setDataset(r.dataset)).catch(() => setDataset([]));
  }, [cid]);

  useEffect(() => { forgeApi.list().then((r) => setApps(r.workflows)).catch(() => setApps([])); }, []);

  const loadList = async () => {
    try { setList((await forgeCharacterApi.list()).characters); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load characters."); }
  };
  useEffect(() => { void loadList(); }, []);

  useEffect(() => {
    if (!cid) { setCharacter(null); setRenders([]); return; }
    forgeCharacterApi.get(cid).then((r) => setCharacter(r.character)).catch((e) => setError(e instanceof Error ? e.message : "Could not open that character."));
  }, [cid]);

  const usable = apps.filter((w) => w.readiness !== "blocked");
  const sheetApps = usable.filter((w) => /sheet/i.test(w.name));
  const sceneApps = usable.filter((w) => !/sheet/i.test(w.name));
  const anchored = !!character?.anchor;
  const checks = character?.history ?? [];
  const avg = checks.filter((h) => h.face_found && h.score != null).map((h) => h.score as number);
  const avgScore = avg.length ? (avg.reduce((a, b) => a + b, 0) / avg.length).toFixed(2) : "–";

  const pickApp = async (id: string, set: (c: SceneControls | null) => void, setId: (s: string) => void) => {
    setId(id); set(null); setError(null);
    if (!id) return;
    try { set(sceneControls((await forgeApi.spec(id)).spec.components)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not read that app."); }
  };

  const create = async () => {
    setError(null);
    try {
      const { character: c } = await forgeCharacterApi.create(newName, newNotes);
      setNewName(""); setNewNotes(""); setCid(c.id); await loadList();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create the character."); }
  };

  const uploadAnchor = async (file: File | undefined) => {
    if (!file || !cid) return;
    setError(null);
    try { setCharacter((await forgeCharacterApi.setAnchor(cid, file)).character); await loadList(); }
    catch (e) { setError(e instanceof Error ? e.message : "The photo could not be used."); }
  };

  const track = (r: Render) => setRenders((rs) => [r, ...rs]);
  const patch = (jobId: string, p: Partial<Render>) => setRenders((rs) => rs.map((r) => (r.jobId === jobId ? { ...r, ...p } : r)));

  const start = async (kind: "sheet" | "scene" | "shot", title: string, appKey: string, c: SceneControls, prompt: string, seedValue: number, preset: string | null = null) => {
    if (!character?.anchor) { setError("Give this character an anchor photo first."); return; }
    if (!c.image || !c.prompt || !c.seed) { setError("This app needs an image input, a prompt and a seed."); return; }
    setError(null);
    try {
      const values = { [c.image]: character.anchor.comfy_name, [c.prompt]: prompt, [c.seed]: seedValue };
      const { job } = await forgeApi.run(appKey, values, "local");
      track({ jobId: job.id, kind, preset, title, state: job.state, seconds: 0, progress: null });
      const final = await watch(job.id, (state, seconds, progress) => { if (alive.current) patch(job.id, { state, seconds, progress }); });
      if (alive.current) patch(job.id, { state: final });
    } catch (e) { setError(e instanceof Error ? e.message : `The ${kind === "sheet" ? "sheet" : "scene"} could not start.`); }
  };

  const addToDataset = async (r: Render) => {
    if (!cid) return;
    setError(null);
    try {
      const res = await forgeShotApi.add(cid, r.jobId, r.preset ?? null);
      setDataset((d) => [res.item, ...d]);
      setCharacter(res.character);
      patch(r.jobId, { inDataset: true });
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add it to the dataset."); }
  };

  const queueShots = async () => {
    if (!sheetControls || !sheetAppId) { setError("Choose an edit model app first."); return; }
    setError(null);
    for (const id of picked) {
      if (!alive.current) return;
      const p = presets.find((x) => x.id === id);
      if (!p) continue;
      await start("shot", p.label, sheetAppId, sheetControls, p.prompt, 42, id);
    }
  };

  const check = async (r: Render) => {
    if (!cid) return;
    setChecking(r.jobId); setError(null);
    try {
      const res = await forgeCharacterApi.check(cid, r.jobId);
      setCharacter(res.character);
      patch(r.jobId, { checked: true, score: res.check.score, faceFound: res.check.face_found, passed: res.check.passed });
    } catch (e) { setError(e instanceof Error ? e.message : "The face check could not run."); }
    finally { setChecking(null); }
  };

  const busy = renders.some((r) => !JOB_DONE.has(r.state));
  const sheetDone = renders.some((r) => r.kind === "sheet" && r.state === "COMPLETED");
  const shotDone = renders.some((r) => r.kind === "shot" && r.state === "COMPLETED");
  // The steps a character goes through, in order. A step is done when its output exists; the first open step is "next".
  const flow: { id: string; label: string; done: boolean; tab: Tab | null; hint: string }[] = [
    { id: "create", label: "Create", done: true, tab: null, hint: "Character saved." },
    { id: "anchor", label: "Anchor", done: anchored, tab: null, hint: anchored ? "Anchor photo set." : "Add one clear photo of one face above." },
    { id: "sheet", label: "Sheet", done: sheetDone, tab: "sheet", hint: "Make a character sheet from the anchor, then check it." },
    { id: "shots", label: "Shots", done: shotDone, tab: "shots", hint: "Render close-up, medium and full-body shots with the same face." },
    { id: "dataset", label: "Dataset", done: dataset.length > 0, tab: "dataset", hint: "Add the good renders to the dataset." },
  ];
  const nextStep = flow.find((s) => !s.done) ?? flow[flow.length - 1];
  const sceneReady = anchored && !!controls?.image && !!controls.prompt && !!controls.seed;
  const sheetReady = anchored && !!sheetControls?.image && !!sheetControls.prompt && !!sheetControls.seed;
  const latestSheet = renders.find((r) => r.kind === "sheet");

  return (
    <div className="page wide influencer">
      <header className="page-head">
        <div>
          <p className="eyebrow">AI Influencer</p>
          <h1>Characters</h1>
          <p className="lede">One character, the same face in every image. Anchor it, make a sheet, generate scenes, and check each face against the anchor.</p>
        </div>
      </header>
      {error && (
        <div className="banner bad infl-error" role="alert">
          <span>{error}</span>
          <button className="secondary" onClick={() => setError(null)} aria-label="Dismiss this message">Dismiss</button>
        </div>
      )}

      <div className="infl-grid">
        <aside className="card infl-roster" aria-labelledby="infl-list-h">
          <h2 id="infl-list-h">Characters</h2>
          {list === null && <div className="skeleton" aria-busy="true" style={{ height: 80 }} />}
          {list?.length === 0 && <p className="muted">No characters yet. Create your first one below.</p>}
          <ul className="infl-list">
            {(list ?? []).map((c) => (
              <li key={c.id}>
                <button className={c.id === cid ? "nav active" : "nav"} aria-current={c.id === cid ? "true" : undefined} onClick={() => setCid(c.id)}>
                  <span>{c.name}</span>
                  <span className={`infl-badge ${c.anchor ? "ok" : "todo"}`}>{c.anchor ? `${c.checks} checks` : "needs anchor"}</span>
                </button>
              </li>
            ))}
          </ul>
          <details className="infl-new" open={list?.length === 0}>
            <summary>New character</summary>
            <form onSubmit={(e) => { e.preventDefault(); void create(); }}>
              <label>Name <input value={newName} maxLength={60} onChange={(e) => setNewName(e.target.value)} /></label>
              <label>Notes <textarea rows={2} maxLength={500} value={newNotes} onChange={(e) => setNewNotes(e.target.value)} placeholder="Optional: style, age, mood" /></label>
              <button type="submit" disabled={!newName.trim()}>Create character</button>
              {!newName.trim() && <Hint>Give the character a name to continue.</Hint>}
            </form>
          </details>
        </aside>

        <main className="infl-main">
          {!character ? (
            <div className="card infl-empty">
              <h2>Start with a character</h2>
              <p className="muted">Pick one on the left, or create one. The anchor photo, sheet and scenes come after.</p>
            </div>
          ) : (
            <>
              <section id="infl-anchor" className="infl-hero card" aria-label={`${character.name} overview`}>
                <div className="infl-hero-photo">
                  {character.anchor ? (
                    <img src={`/api/forge/uploads/${character.anchor.comfy_name}`} alt={`Anchor photo of ${character.name}`} />
                  ) : (
                    <div className="infl-anchor-empty" aria-hidden="true">No anchor yet</div>
                  )}
                </div>
                <div className="infl-hero-body">
                  <h2>{character.name}</h2>
                  {character.notes && <p className="muted">{character.notes}</p>}
                  <dl className="infl-stats">
                    <div><dt>Checks</dt><dd>{checks.length}</dd></div>
                    <div><dt>Avg similarity</dt><dd>{avgScore}</dd></div>
                    <div><dt>Renders</dt><dd>{renders.length}</dd></div>
                  </dl>
                  <label className="infl-file">{anchored ? "Replace anchor photo" : "Add anchor photo"}
                    <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => void uploadAnchor(e.target.files?.[0])} />
                  </label>
                  {!anchored && <Hint>Add one clear photo of one face (PNG, JPEG or WebP). Everything else uses it.</Hint>}
                </div>
              </section>

              <ol className="infl-flow" aria-label="Character flow">
                {flow.map((st, i) => (
                  <li key={st.label} className={st.done ? "done" : st.id === nextStep.id ? "now" : ""}>
                    <button className="infl-flow-step" aria-current={st.id === nextStep.id ? "step" : undefined}
                      onClick={() => st.tab ? setTab(st.tab) : document.getElementById("infl-anchor")?.scrollIntoView({ behavior: "smooth", block: "center" })}>
                      <span className="infl-flow-dot" aria-hidden="true">{st.done ? "✓" : i + 1}</span>
                      <span>{st.label}</span>
                    </button>
                  </li>
                ))}
              </ol>
              <p className="infl-next" role="status">
                <strong>Next: {nextStep.label}.</strong> {nextStep.hint}
              </p>

              <div className="infl-tabs" role="tablist" aria-label="Character workflow">
                {([["sheet", "Character sheet"], ["shots", "Shots"], ["dataset", `Dataset (${dataset.length})`], ["scenes", "Scenes"], ["create", "Create from idea"], ["checks", "Face checks"]] as const).map(([id, label]) => (
                  <button key={id} role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls={`panel-${id}`}
                    className={tab === id ? "infl-tab on" : "infl-tab"} onClick={() => setTab(id)}>{label}</button>
                ))}
              </div>

              <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="card infl-panel">
                {tab === "sheet" && (
                  <>
                    <p className="muted">Uses the anchor as the base reference for an edit model, to draw front, side and back views.</p>
                    {!anchored && <Hint>Add the anchor photo first.</Hint>}
                    {sheetApps.length === 0 && <Hint>No sheet app is imported yet. Import a character sheet workflow to use this tab.</Hint>}
                    <div className="infl-fields">
                      <label>Sheet app
                        <select value={sheetAppId} onChange={(e) => void pickApp(e.target.value, setSheetControls, setSheetAppId)} disabled={!anchored}>
                          <option value="">Choose a sheet app</option>
                          {sheetApps.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                      </label>
                      <label>Sheet prompt
                        <textarea rows={3} value={sheetPrompt} onChange={(e) => setSheetPrompt(e.target.value)} disabled={!anchored} />
                      </label>
                    </div>
                    <button onClick={() => sheetControls && void start("sheet", `${character.name} sheet`, sheetAppId, sheetControls, sheetPrompt, 42)}
                      disabled={!sheetReady || busy}>
                      {busy ? "Another render is running" : "Make character sheet"}
                    </button>
                    {sheetAppId && !sheetReady && anchored && <Hint>This app needs an image input, a prompt and a seed.</Hint>}
                    {latestSheet && latestSheet.state === "COMPLETED" && (
                      <p className="muted">Latest sheet is in the gallery below.</p>
                    )}
                  </>
                )}

                {tab === "scenes" && (
                  <>
                    {!anchored && <Hint>Add the anchor photo first.</Hint>}
                    <div className="infl-fields">
                      <label>App to use
                        <select value={appId} onChange={(e) => void pickApp(e.target.value, setControls, setAppId)} disabled={!anchored}>
                          <option value="">Choose an imported app</option>
                          {sceneApps.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                      </label>
                      {appId && controls && (!controls.image || !controls.prompt || !controls.seed) && (
                        <Hint>This app is missing an image input, a prompt or a seed, so it cannot take an anchor. Pick another app.</Hint>
                      )}
                      <label>Scene prompt
                        <textarea rows={3} value={scenePrompt} onChange={(e) => setScenePrompt(e.target.value)} placeholder="Where she is, what she is doing, the light" disabled={!anchored} />
                      </label>
                      <label className="infl-seed">Seed <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} disabled={!anchored} /></label>
                    </div>
                    <button onClick={() => controls && void start("scene", scenePrompt.slice(0, 40) || "Scene", appId, controls, scenePrompt, seed)}
                      disabled={!sceneReady || busy}>
                      {busy ? "Another render is running" : "Generate scene"}
                    </button>
                  </>
                )}

                {tab === "shots" && (
                  <>
                    <p className="muted">Shots keep the same character across framings. Each shot is a full render, so queue only what you need.</p>
                    {!anchored && <Hint>Add the anchor photo first.</Hint>}
                    {sheetApps.length === 0 && <Hint>No sheet app is imported yet. Import a character sheet workflow to use this tab.</Hint>}
                    <label>Edit model app
                      <select value={sheetAppId} onChange={(e) => void pickApp(e.target.value, setSheetControls, setSheetAppId)} disabled={!anchored}>
                        <option value="">Choose an edit model app</option>
                        {sheetApps.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                      </select>
                    </label>
                    <p className="infl-field-note muted">Same app as the character sheet, so you choose it once.</p>
                    <div className="infl-chips" role="group" aria-label="Shot presets">
                      {presets.map((p) => (
                        <button key={p.id} className={picked.includes(p.id) ? "infl-chip on" : "infl-chip"} aria-pressed={picked.includes(p.id)}
                          onClick={() => setPicked((v) => (v.includes(p.id) ? v.filter((x) => x !== p.id) : [...v, p.id]))}>
                          {p.label}
                        </button>
                      ))}
                    </div>
                    <p className="muted">{picked.length} shot{picked.length === 1 ? "" : "s"} · about {picked.length * 15}–{picked.length * 20} minutes on this GPU</p>
                    <button onClick={() => void queueShots()} disabled={!anchored || !sheetReady || picked.length === 0 || busy}>
                      {busy ? "A render is running" : `Render ${picked.length} shot${picked.length === 1 ? "" : "s"}`}
                    </button>
                  </>
                )}

                {tab === "dataset" && (
                  dataset.length === 0 ? (
                    <p className="muted">No dataset items yet. Add a finished render from the gallery with "Add to dataset".</p>
                  ) : (
                    <div className="infl-cards">
                      {dataset.map((d) => (
                        <article key={d.id} className="infl-card">
                          <div className="infl-card-img"><img src={forgeShotApi.imageUrl(character.id, d.id)} alt={d.caption} /></div>
                          <div className="infl-card-body">
                            <strong>{d.preset ?? "render"}</strong>
                            <span className="infl-caption">{d.caption}</span>
                          </div>
                        </article>
                      ))}
                    </div>
                  )
                )}

                {tab === "create" && (
                  <Creator apps={apps} character={character} onAnchor={setCharacter} />
                )}

                {tab === "checks" && (
                  checks.length === 0 ? (
                    <p className="muted">No checks yet. Open a render in the gallery and check its face against the anchor.</p>
                  ) : (
                    <ul className="infl-history">
                      {checks.slice().reverse().map((h) => (
                        <li key={h.at + h.job_id}>
                          <span className="muted">{new Date(h.at).toLocaleString()}</span>{" "}
                          {h.face_found ? `similarity ${h.score?.toFixed(3)}, ${h.passed ? "passed" : "below threshold"}` : "no face found"}
                        </li>
                      ))}
                    </ul>
                  )
                )}
              </section>

              <section className="infl-gallery" aria-labelledby="gallery-h">
                <h3 id="gallery-h">Renders this session</h3>
                {renders.length === 0 && <p className="muted">Renders you start appear here with their progress.</p>}
                <div className="infl-cards">
                  {renders.map((r) => (
                    <article key={r.jobId} className="infl-card">
                      <div className="infl-card-img">
                        {r.state === "COMPLETED" ? (
                          <img src={forgeApi.runOutputUrl(r.jobId)} alt={`${r.kind === "sheet" ? "Sheet" : "Scene"}: ${r.title}`} />
                        ) : (
                          <ProgressPanel state={r.state} seconds={r.seconds} progress={r.progress} />
                        )}
                      </div>
                      <div className="infl-card-body">
                        <strong>{r.title}</strong>
                        <span className="muted">{r.kind === "sheet" ? "Sheet" : "Scene"} · {STAGE_LABEL(r.state)}</span>
                        {r.checked && (
                          <span className={`infl-badge ${r.faceFound && r.passed ? "ok" : "todo"}`}>
                            {r.faceFound ? `similarity ${r.score?.toFixed(2)}` : "no face"}
                          </span>
                        )}
                        {r.state === "COMPLETED" && !r.inDataset && (
                          <button className="secondary" onClick={() => void addToDataset(r)}>Add to dataset</button>
                        )}
                        {r.inDataset && <span className="infl-badge ok">In dataset</span>}
                        {r.state === "COMPLETED" && !r.checked && (
                          <button className="secondary" onClick={() => void check(r)} disabled={checking === r.jobId}>
                            {checking === r.jobId ? "Checking face…" : "Check face"}
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
                <p className="infl-disclaimer muted">A face check is a screening score against the anchor, not proof of identity. Its threshold is provisional.</p>
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function STAGE_LABEL(state: string): string {
  return ({ COMPLETED: "done", FAILED: "failed", CANCELLED: "cancelled", INTERRUPTED: "interrupted", RECOVERY_REQUIRED: "needs attention" } as Record<string, string>)[state] ?? "in progress";
}
