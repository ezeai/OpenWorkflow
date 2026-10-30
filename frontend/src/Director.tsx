import { Tabs } from "./Tabs";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContinuityPanel } from "./Continuity";
import { ApiError, CAMERA_OPTIONS, directorApi, jobsApi, projectsApi, type DirectorView, type GenerationPreflight, type Proposal, type ShotView, type TakeView } from "./api";

const GENERATABLE = ["img-z-image-turbo"];
const TAKE_TEXT: Record<string, string> = {
  requested: "Requested", running: "Running", generated: "Generated", failed: "Failed",
  cancelled: "Cancelled", approved: "Approved", archived: "Archived",
};

type Tab = "shots" | "script" | "plan" | "continuity";

const LABELS: Record<string, string> = {
  shot_size: "Framing", camera_angle: "Camera angle", camera_move: "Camera movement (intent)", lens: "Lens intent",
  depth: "Depth of field", composition: "Composition", move_intensity: "Motion intensity",
};

const CAP_TEXT: Record<string, string> = {
  honoured: "Honoured by workflow",
  prompt_only: "Prompt guidance only",
  unsupported: "Not executed by this workflow. Kept as metadata.",
  not_mapped: "No workflow chosen",
};

const CAP_KEY: Record<string, string> = {
  shot_size: "shot_size", camera_angle: "camera_angle", camera_move: "camera_move", lens: "lens",
  depth: "depth", composition: "composition", move_intensity: "move_intensity",
};

const label = (s: string) => s.replace(/_/g, " ");

function errorText(e: unknown) {
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function Director({ projectId }: { projectId: string }) {
  const [data, setData] = useState<DirectorView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("shots");
  const [selected, setSelected] = useState<string | null>(null);
  const [sceneFilter, setSceneFilter] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const revRef = useRef(0);
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const apply = useCallback((d: DirectorView) => {
    revRef.current = d.revision;
    setData(d);
    setErr(null);
  }, []);

  const load = useCallback(() => directorApi.get(projectId).then(apply).catch((e) => setErr(errorText(e))), [projectId, apply]);
  useEffect(() => { void load(); }, [load]);

  // while a take is running, refresh so its status and image appear without a reload
  const anyRunning = !!data?.shots.some((s) => s.takes.some((t) => t.state === "running"));
  useEffect(() => {
    if (!anyRunning) return;
    const t = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(t);
  }, [anyRunning, load]);

  // every write goes through one queue and always uses the newest revision the server confirmed
  const write = useCallback((fn: (rev: number) => Promise<DirectorView>, ok?: string) => {
    const task = queue.current.then(async () => {
      setBusy(true);
      setNotice(null);  // a status from an earlier action must not outlive the next one
      try {
        const d = await fn(revRef.current);
        apply(d);
        if (ok) setNotice(ok);
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setErr(`${e.message}`);
          await load();
        } else {
          setErr(errorText(e));
        }
      } finally {
        setBusy(false);
      }
    });
    queue.current = task.catch(() => undefined);
    return task;
  }, [apply, load]);

  if (!data) {
    return err ? <div className="page"><div className="banner bad" role="alert">{err}</div></div>
      : <div className="page"><div className="skeleton" aria-busy="true" style={{ height: 160 }} /></div>;
  }

  const shots = data.shots.filter((s) => !sceneFilter || s.scene_id === sceneFilter);
  const current = data.shots.find((s) => s.id === selected) ?? null;

  return (
    <div className="page wide director">
      <header className="page-head">
        <div>
          <h1>Director</h1>
          <p className="lede">Plan the scene, then direct each shot. Camera settings are intent. Shown beside each control is what the chosen workflow can honour.</p>
        </div>
        <span className="save-status" role="status">{busy ? "Saving…" : notice ?? ""}</span>
      </header>
      {err && <div className="banner bad" role="alert">{err} <button className="link" onClick={() => void load()}>Reload</button></div>}
      {data.recovery_note && <div className="banner warn" role="status">{data.recovery_note}</div>}
      <div className="banner info" role="status">Video generation is not connected yet. Takes are generated as still images; camera moves are written into the prompt only.</div>

      <Tabs
        label="Director sections"
        value={tab}
        onChange={setTab}
        items={(["shots", "script", "plan", "continuity"] as Tab[]).map((t) => ({
          id: t,
          label: t === "shots" ? `Shots (${data.shots.length})` : t === "script" ? "Script" : t === "plan" ? "Plan from script" : "Continuity",
        }))}
      />

      {tab === "continuity" && <ContinuityPanel projectId={projectId} />}
      {tab === "script" && <ScriptPanel data={data} onSave={(text) => write((rev) => directorApi.setScript(projectId, rev, text), "Script saved")} />}
      {tab === "plan" && (
        <PlanPanel projectId={projectId} revision={data.revision} onApplied={(d, n) => { apply(d); setNotice(`${n} shot(s) added. Existing shots were not changed.`); setTab("shots"); }} />
      )}

      {tab === "shots" && (
        <div className="director-grid">
          <nav className="scene-nav" aria-label="Scenes">
            <h2>Scenes</h2>
            <button className={!sceneFilter ? "scene active" : "scene"} onClick={() => setSceneFilter(null)}>All shots</button>
            {data.scenes.length === 0 && <p className="muted small">No scenes yet. Write a script, then plan from it.</p>}
            {data.scenes.map((s) => (
              <button key={s.id} className={sceneFilter === s.id ? "scene active" : "scene"} onClick={() => setSceneFilter(s.id)}>
                {s.title}<span className="small muted"> · {data.shots.filter((x) => x.scene_id === s.id).length} shots</span>
              </button>
            ))}
            <button className="ghost" onClick={() => write((rev) => directorApi.addShot(projectId, rev, sceneFilter ?? undefined), "Shot added")}>Add shot</button>
            {data.suggestions.length > 0 && (
              <section className="suggestions" aria-label="Coverage suggestions">
                <h2>Suggestions</h2>
                <ul>
                  {data.suggestions.map((s) => (
                    <li key={s.key}>
                      <div className="small">{s.message}</div>
                      <div className="small muted">{s.why}</div>
                      <button className="ghost" onClick={() => write((rev) => directorApi.dismiss(projectId, s.key, rev), "Suggestion dismissed")}>Dismiss</button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </nav>

          <section className="shot-list" aria-label="Shot list">
            {shots.length === 0 && (
              <div className="card empty">
                <h2>No shots yet</h2>
                <p className="muted">Write a script, then use “Plan from script” to propose shots, or add one by hand.</p>
              </div>
            )}
            <ol className="shots">
              {shots.map((s, i) => (
                <ShotCard
                  key={s.id}
                  projectId={projectId}
                  shot={s}
                  names={{
                    characters: data.characters.filter((c) => s.character_ids.includes(c.id)).map((c) => c.name),
                    location: data.locations.find((l) => l.id === s.location_id)?.name ?? null,
                  }}
                  active={selected === s.id}
                  first={i === 0}
                  last={i === shots.length - 1}
                  onSelect={() => setSelected(s.id)}
                  onMove={(dir) => {
                    const order = data.shots.map((x) => x.id);
                    const a = order.indexOf(s.id);
                    const b = dir === "up" ? a - 1 : a + 1;
                    if (b < 0 || b >= order.length) return;
                    [order[a], order[b]] = [order[b], order[a]];
                    write((rev) => directorApi.reorder(projectId, rev, order), "Order saved");
                  }}
                  onDuplicate={() => write((rev) => directorApi.duplicate(projectId, s.id, rev), "Shot duplicated")}
                  onToggle={() => write((rev) => directorApi.patchShot(projectId, s.id, rev, { enabled: !s.enabled }), s.enabled ? "Shot disabled" : "Shot enabled")}
                  onRemove={() => write((rev) => directorApi.remove(projectId, s.id, rev), "Shot removed")}
                />
              ))}
            </ol>
          </section>

          <aside className="detail inspector" aria-label="Shot inspector">
            {!current && <p className="muted">Select a shot to edit its direction.</p>}
            {current && (
              <Inspector
                key={current.id}
                data={data}
                projectId={projectId}
                shot={current}
                onPatch={(fields, preset) => write((rev) => directorApi.patchShot(projectId, current.id, rev, fields, preset))}
                onRequestTake={(note) => write((rev) => directorApi.requestTake(projectId, current.id, rev, note), "Take recorded. Not generated.")}
                onGenerate={async () => {
                  try {
                    await jobsApi.generate(projectId, current.id, revRef.current);
                    await load();
                    setNotice("Generation queued. Track it on the Jobs page.");
                  } catch (e) {
                    setErr(e instanceof ApiError && e.status === 409 ? `${e.message} Reload and try again.` : errorText(e));
                  }
                }}
                onApproveTake={(t) => { void write((rev) => directorApi.approveTake(projectId, current.id, t.id, rev), `Take ${t.number} chosen`); }}
                onUnapproveTake={(t) => { void write((rev) => directorApi.unapproveTake(projectId, current.id, t.id, rev), `Take ${t.number} released`); }}
              />
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function ShotCard({ projectId, shot, names, active, first, last, onSelect, onMove, onDuplicate, onToggle, onRemove }: {
  projectId: string; shot: ShotView; names: { characters: string[]; location: string | null }; active: boolean; first: boolean; last: boolean;
  onSelect: () => void; onMove: (d: "up" | "down") => void; onDuplicate: () => void; onToggle: () => void; onRemove: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const errors = shot.warnings.filter((w) => w.level === "error").length;
  const warns = shot.warnings.filter((w) => w.level === "warn").length;
  const chosen = shot.takes.find((t) => t.state === "approved") ?? null;
  const generated = shot.takes.filter((t) => t.state === "generated" || t.state === "approved").length;
  const pending = shot.takes.filter((t) => t.state === "running").length;
  return (
    <li className={`shot-card ${active ? "selected" : ""} ${shot.enabled ? "" : "disabled"}`}>
      <button className="shot-main" onClick={onSelect} aria-pressed={active} aria-label={`Shot ${shot.number}, ${label(shot.shot_size)}`}>
        <span className="shot-num">{shot.number}</span>
        {chosen?.asset_id && <img className="shot-chosen-thumb" src={projectsApi.fileUrl(projectId, chosen.asset_id)} alt={""} aria-hidden="true" />}
        <span className="shot-text">
          <strong>{label(shot.shot_size)}</strong> · {label(shot.camera_move)}
          <span className="small muted"> · {shot.scene_title || "unscripted"}</span>
          <span className="small muted block">{shot.texts.beat || shot.texts.action || shot.texts.purpose || "No description"}</span>
        </span>
      </button>
      <div className="shot-meta small">
        <span className="pill muted">{label(shot.shot_size)}</span>
        {names.characters.map((n) => <span key={n} className="pill info">{n}</span>)}
        {names.location && <span className="pill muted">{names.location}</span>}
        <span className="pill muted">Camera: {label(shot.camera_move)} · prompt only</span>
        {!shot.enabled && <span className="pill muted">Disabled</span>}
        {chosen && <span className="pill good">Take {chosen.number} chosen</span>}
        {shot.takes.length > 0 && generated > 0 && !chosen && <span className="pill info">{generated} take{generated > 1 ? "s" : ""} · none chosen yet</span>}
        {shot.takes.length > 0 && generated === 0 && <span className="pill info">{shot.takes.length} take{shot.takes.length > 1 ? "s" : ""} · not generated{pending > 0 ? ` · ${pending} running` : ""}</span>}
        {errors > 0 && <span className="pill bad">{errors} error(s)</span>}
        {warns > 0 && <span className="pill warn">{warns} warning(s)</span>}
      </div>
      <div className="shot-actions">
        <button className="ghost" disabled={first} aria-label="Move up" onClick={() => onMove("up")}>↑</button>
        <button className="ghost" disabled={last} aria-label="Move down" onClick={() => onMove("down")}>↓</button>
        <button className="ghost" onClick={onDuplicate}>Duplicate</button>
        <button className="ghost" onClick={onToggle}>{shot.enabled ? "Disable" : "Enable"}</button>
        {shot.takes.length > 0 && <span className="small muted">Has takes: disable instead of removing</span>}
        {shot.takes.length === 0 && !confirming && <button className="ghost" onClick={() => setConfirming(true)}>Remove</button>}
        {shot.takes.length === 0 && confirming && (
          <>
            <button className="danger-btn" onClick={() => { setConfirming(false); onRemove(); }}>Confirm remove</button>
            <button className="ghost" onClick={() => setConfirming(false)}>Keep</button>
          </>
        )}
      </div>
    </li>
  );
}

function Inspector({ data, projectId, shot, onPatch, onRequestTake, onGenerate, onApproveTake, onUnapproveTake }: {
  data: DirectorView; projectId: string; shot: ShotView;
  onPatch: (fields: Record<string, unknown>, preset?: string) => void;
  onRequestTake: (note: string) => void;
  onGenerate: () => void;
  onApproveTake: (t: TakeView) => void;
  onUnapproveTake: (t: TakeView) => void;
}) {
  const canGenerate = !!shot.workflow_id && GENERATABLE.includes(shot.workflow_id) && shot.enabled;
  const [beat, setBeat] = useState(shot.texts.beat ?? "");
  const [action, setAction] = useState(shot.texts.action ?? "");
  const [note, setNote] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const [check, setCheck] = useState<GenerationPreflight | null>(null);
  const [checking, setChecking] = useState(false);
  const runCheck = async () => {
    setChecking(true);
    try { setCheck(await jobsApi.preflight(projectId, shot.id)); }
    catch (e) { setCheck({ ready: false, problems: [errorText(e)], params: {}, prompt: { positive: "", negative: "" }, notes: [], models: [], nodes_checked: 0 }); }
    finally { setChecking(false); }
  };
  const textsSaved = useRef(shot.texts);

  const saveTexts = (next: Record<string, string>) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (JSON.stringify(next) !== JSON.stringify(textsSaved.current)) {
        textsSaved.current = next;
        onPatch({ texts: next });
      }
    }, 700);
  };
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const toggleIn = (key: "character_ids" | "prop_ids", id: string) => {
    const cur = shot[key];
    onPatch({ [key]: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  };

  const warnings = shot.warnings;
  return (
    <div className="detail-body">
      <h2 className="detail-title">Shot {shot.number}</h2>
      <p className="small muted">{shot.scene_title || "Unscripted"}</p>

      {warnings.length > 0 && (
        <ul className="warnings" aria-label="Shot warnings">
          {warnings.map((w, i) => <li key={i} className={`w-${w.level}`}>{w.message}</li>)}
        </ul>
      )}

      <label className="field">
        <span className="small">Preset (fills camera fields only; your text is kept)</span>
        <select aria-label="Preset" value={shot.preset ?? ""} onChange={(e) => e.target.value && onPatch({}, e.target.value)}>
          <option value="">Choose a preset…</option>
          {data.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>

      {(Object.keys(CAMERA_OPTIONS) as (keyof typeof CAMERA_OPTIONS)[]).map((field) => {
        const cap = shot.capabilities[CAP_KEY[field] ?? field] as string | undefined;
        const value = (shot as unknown as Record<string, string>)[field];
        return (
          <label key={field} className="field">
            <span className="small">{LABELS[field]} <span className={`cap cap-${cap ?? "none"}`}>{cap ? CAP_TEXT[cap] ?? cap : ""}</span></span>
            <select aria-label={LABELS[field]} value={value} onChange={(e) => onPatch({ [field]: e.target.value })}>
              {CAMERA_OPTIONS[field].map((o) => <option key={o} value={o}>{label(o)}</option>)}
            </select>
          </label>
        );
      })}

      <label className="field">
        <span className="small">Duration (seconds)</span>
        <input type="number" min={0.5} max={30} step={0.5} aria-label="Duration in seconds" value={shot.duration_s}
          onChange={(e) => { const v = Number(e.target.value); if (v >= 0.5 && v <= 30) onPatch({ duration_s: v }); }} />
      </label>

      <label className="field">
        <span className="small">Workflow (controls what can be honoured)</span>
        <select aria-label="Workflow" value={shot.workflow_id ?? ""} onChange={(e) => onPatch({ workflow_id: e.target.value || null })}>
          <option value="">No workflow</option>
          {data.workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </label>

      <fieldset className="field">
        <legend className="small">Characters in shot</legend>
        {data.characters.length === 0 && <p className="muted small">Add characters in the Characters page.</p>}
        {data.characters.map((c) => (
          <label key={c.id} className="check"><input type="checkbox" checked={shot.character_ids.includes(c.id)} onChange={() => toggleIn("character_ids", c.id)} /> {c.name}</label>
        ))}
      </fieldset>

      <label className="field">
        <span className="small">Location</span>
        <select aria-label="Location" value={shot.location_id ?? ""} onChange={(e) => onPatch({ location_id: e.target.value || null })}>
          <option value="">None</option>
          {data.locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
      </label>

      <label className="field">
        <span className="small">Beat (what this shot shows)</span>
        <textarea rows={2} aria-label="Beat" value={beat} maxLength={4000} onChange={(e) => { setBeat(e.target.value); saveTexts({ ...shot.texts, beat: e.target.value, action }); }} />
      </label>
      <label className="field">
        <span className="small">Action</span>
        <textarea rows={2} aria-label="Action" value={action} maxLength={4000} onChange={(e) => { setAction(e.target.value); saveTexts({ ...shot.texts, action: e.target.value, beat }); }} />
      </label>

      <section className="caps" aria-label="Capability notes">
        <h3>What the workflow does with this shot</h3>
        <p className="small muted">{shot.capabilities._note as string}</p>
      </section>

      <section className="takes" aria-label="Takes">
        <h3>Takes</h3>
        {shot.takes.length === 0 && <p className="muted small">No takes yet.</p>}
        <ul className="takes-list">
          {[...shot.takes].reverse().map((t) => {
            const chosen = t.state === "approved";
            return (
              <li key={t.id} className={`small take-row ${chosen ? "chosen" : ""}`}>
                {t.asset_id && <img className="take-thumb" src={projectsApi.fileUrl(projectId, t.asset_id)} alt={`Take ${t.number} output`} />}
                <div>
                  <strong>Take {t.number}</strong> · {TAKE_TEXT[t.state] ?? t.state}
                  {chosen && <span className="take-chosen-mark"> · chosen take</span>}
                  {t.state === "requested" && <span className="muted"> · not generated</span>}
                  {t.note && <div className="muted">{t.note}</div>}
                </div>
                {t.state === "generated" && t.asset_id && (
                  <button className="ghost take-choose" onClick={() => onApproveTake(t)}>Choose this take</button>
                )}
                {chosen && <button className="ghost take-choose" onClick={() => onUnapproveTake(t)}>Release</button>}
              </li>
            );
          })}
        </ul>
        {shot.takes.some((t) => t.state === "approved") && (
          <p className="small muted">A chosen take stays with the shot across edits; choosing another releases this one, nothing is deleted.</p>
        )}
        <div className="actions" style={{ marginTop: 6 }}>
          {canGenerate
            ? <>
                <button className="secondary" disabled={checking} onClick={() => void runCheck()}>{checking ? "Checking…" : "Check before generating"}</button>
                <button onClick={onGenerate}>Generate take (image)</button>
              </>
            : <p className="small muted">{shot.workflow_id ? "No generator is connected for this workflow yet." : "Choose a workflow to generate."}</p>}
        </div>
        {check && (
          <section className="card preflight" aria-label="Generation check">
            {check.ready
              ? <div className="banner good small">Ready. ComfyUI has every node and model file this take needs. Nothing is queued yet.</div>
              : <div className="banner bad small" role="alert">
                  <strong>Not ready.</strong>
                  <ul>{check.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
                </div>}
            <details className="small" open>
              <summary>Prompt sent to the model</summary>
              <p><strong>Positive:</strong> <code>{check.prompt.positive || "(empty)"}</code></p>
              <p><strong>Negative:</strong> <code>{check.prompt.negative || "(empty)"}</code></p>
            </details>
            <div className="small">Models: {check.models.map((m) => `${m.role}: ${m.name}${m.identity_confirmed ? "" : " (identity not confirmed)"}`).join("; ") || "none needed"}</div>
            {check.notes.map((n, i) => <div key={i} className="small muted">{n}</div>)}
            <div className="actions" style={{ marginTop: 6 }}>
              <button disabled={!check.ready} onClick={() => { setCheck(null); onGenerate(); }}>Queue this take</button>
              <button className="ghost" onClick={() => setCheck(null)}>Close check</button>
            </div>
          </section>
        )}
        <div className="inline-form">
          <label className="small">Note for next take
            <input aria-label="Take note" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button disabled={!shot.enabled} onClick={() => { onRequestTake(note); setNote(""); }}>Request take</button>
        </div>
        {!shot.enabled && <p className="small muted">Enable the shot to request a take.</p>}
        <p className="small muted">A recorded take keeps the settings above. Generating runs the workflow on this GPU; earlier takes are kept.</p>
      </section>
    </div>
  );
}

function ScriptPanel({ data, onSave }: { data: DirectorView; onSave: (text: string) => void }) {
  const [text, setText] = useState(data.script_text);
  const [dirty, setDirty] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const change = (v: string) => {
    setText(v);
    setDirty(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { onSave(v); setDirty(false); }, 900);
  };
  return (
    <section className="card" aria-labelledby="script-h">
      <h2 id="script-h">Script</h2>
      <p className="small muted">Use INT. or EXT. lines for scene headings. Lines like “NAME: words” are dialogue. Anything else is action. Saves as you type.</p>
      <textarea className="script" rows={16} aria-label="Script text" value={text} maxLength={200000} onChange={(e) => change(e.target.value)} />
      <p className="small">{dirty ? "Unsaved changes…" : `${data.scenes.length} scene(s) detected`}</p>
    </section>
  );
}

function PlanPanel({ projectId, revision, onApplied }: { projectId: string; revision: number; onApplied: (d: DirectorView, n: number) => void }) {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [planRev, setPlanRev] = useState(revision);

  const propose = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await directorApi.plan(projectId);
      setProposals(r.proposals);
      setPlanRev(r.revision);
      setPicked(new Set(r.proposals.map((_, i) => i)));
    } catch (e) { setErr(errorText(e)); }
    finally { setBusy(false); }
  };

  const apply = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await directorApi.applyPlan(projectId, planRev, [...picked].sort((a, b) => a - b));
      onApplied(r, r.created.length);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? "The project changed since this plan was made. Propose again." : errorText(e));
    } finally { setBusy(false); }
  };

  return (
    <section className="card" aria-labelledby="plan-h">
      <h2 id="plan-h">Plan from script</h2>
      <p className="small muted">Rules only, no AI. Each proposal shows the rule behind it. Nothing is added until you apply your selection.</p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button onClick={propose} disabled={busy}>{busy ? "Working…" : proposals ? "Propose again" : "Propose shots"}</button>
      </div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {proposals && proposals.length === 0 && <p className="muted">The script has no scenes yet.</p>}
      {proposals && proposals.length > 0 && (
        <>
          <ol className="proposals">
            {proposals.map((p, i) => (
              <li key={i} className="proposal">
                <label className="check">
                  <input type="checkbox" checked={picked.has(i)} aria-label={`Include proposal ${i + 1}`}
                    onChange={(e) => setPicked((prev) => { const n = new Set(prev); e.target.checked ? n.add(i) : n.delete(i); return n; })} />
                  <strong>{label(p.shot_size)}</strong> · {label(p.camera_move)} · <span className="muted">{p.scene_title}</span>
                </label>
                <div className="small">{p.texts.beat}</div>
                <div className="small muted">{p.why}</div>
              </li>
            ))}
          </ol>
          <div className="actions">
            <button onClick={apply} disabled={busy || picked.size === 0}>Add {picked.size} selected shot(s)</button>
          </div>
        </>
      )}
    </section>
  );
}
