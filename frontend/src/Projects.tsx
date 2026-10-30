import { Tabs } from "./Tabs";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, projectsApi, type Entity, type EntityKindId, type ProjectSummary, type ProjectView } from "./api";

const KIND_TABS: { id: EntityKindId; label: string; singular: string }[] = [
  { id: "character", label: "Characters", singular: "character" },
  { id: "world", label: "Worlds", singular: "world" },
  { id: "style", label: "Styles", singular: "style" },
  { id: "prop", label: "Props", singular: "prop" },
];

const ROLE_LABELS: Record<string, string> = {
  face: "Face", three_quarter: "Three-quarter view", profile: "Side profile", full_body: "Full body",
  hair: "Hair", clothing: "Clothing", expression: "Expression", other: "Other", reference: "Reference",
};

type SavePayload = { name: string; fields: Record<string, string>; refs: { asset_id: string; role: string }[] };

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "error"; message: string }
  | { kind: "offline" }
  | { kind: "conflict"; message: string };

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}

/** For a character, prefer the face shot as the card cover; otherwise the first usable reference. */
function entityCover(project: ProjectView, e: Entity): { asset_id: string } | null {
  const statusById = new Map(project.assets.map((a) => [a.id, a.status]));
  const usable = e.refs.filter((r) => statusById.get(r.asset_id) === "ok");
  if (e.kind === "character") return usable.find((r) => r.role === "face") ?? usable[0] ?? null;
  return usable[0] ?? null;
}

function SaveStatus({ state, onRetry, onReload }: { state: SaveState; onRetry?: () => void; onReload?: () => void }) {
  if (state.kind === "idle") return null;
  if (state.kind === "saving") return <span className="save-status" role="status">Saving…</span>;
  if (state.kind === "saved") return <span className="save-status ok" role="status">Saved {state.at}</span>;
  if (state.kind === "offline")
    return (
      <span className="save-status warn" role="status">
        Offline. Your changes stay on this screen until the connection returns. <button className="link" onClick={onRetry}>Retry</button>
      </span>
    );
  if (state.kind === "conflict")
    return (
      <span className="save-status bad" role="alert">
        {state.message} <button className="link" onClick={onReload}>Reload latest</button>
      </span>
    );
  return (
    <span className="save-status bad" role="alert">
      Save failed: {state.message} <button className="link" onClick={onRetry}>Try again</button>
    </span>
  );
}

function clock(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ProjectsPage({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ProjectSummary[] | null>(null);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => projectsApi.list().then((r) => setItems(r.projects)).catch((e) => setErr(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);

  const importFile = async (file: File) => {
    setErr(null);
    try {
      const r = await fetch("/api/projects/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Import failed (${r.status})`);
      await load();
      onOpen(data.project_id);
    } catch (e) {
      setErr(errorText(e));
    }
  };

  const create = async () => {
    if (!title.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const p = await projectsApi.create(title.trim());
      onOpen(p.id);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page wide">
      <header className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="lede">A project keeps its characters, worlds, reference images and version history together. Changes save as you type.</p>
        </div>
      </header>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); void create(); }}>
        <label className="small">New project title
          <input aria-label="New project title" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Rain on the night market" />
        </label>
        <button type="submit" disabled={busy || !title.trim()}>{busy ? "Creating…" : "Create project"}</button>
      </form>
      <label className="small" style={{ display: "block", marginTop: 14 }}>Import a project (.zip from Export)
        <input aria-label="Import project zip" type="file" accept=".zip,application/zip" onChange={(e) => { const f = e.target.files?.[0]; if (f) void importFile(f); e.target.value = ""; }} />
      </label>
      <p className="small muted">Models are not in exports. The import lists which models to install.</p>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {!items && !err && <div className="skeleton" aria-busy="true" style={{ height: 120, marginTop: 18 }} />}
      {items && items.length === 0 && (
        <section className="card empty" style={{ marginTop: 18 }}>
          <h2>No projects yet</h2>
          <p className="muted">Create a project above. Characters and worlds you add to it are saved with it.</p>
        </section>
      )}
      {items && items.length > 0 && (
        <ul className="project-list project-grid" style={{ marginTop: 18 }}>
          {items.map((p) => (
            <li key={p.id} className="card project-row">
              <div className="project-row-meta">
                <strong>{p.readable ? p.title : "Unreadable project"}</strong>
                <div className="small muted">{p.readable ? `Last saved ${new Date(p.updated_at).toLocaleString()}` : p.note}</div>
                <div className="small muted"><code>{p.id}</code></div>
              </div>
              {p.readable && (
                <div className="project-row-actions" style={{ marginTop: 0 }}>
                  <a className="secondary" style={{ padding: "9px 14px", borderRadius: 10, border: "1px solid var(--line)", color: "var(--text)", textDecoration: "none", display: "inline-block" }}
                    href={`/api/projects/${p.id}/export`}>Export</a>
                  <button className="secondary" onClick={() => onOpen(p.id)}>Open</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ProjectWorkspace({ projectId, initialKind, onBack }: { projectId: string; initialKind: EntityKindId; onBack: () => void }) {
  const [project, setProject] = useState<ProjectView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [kind, setKind] = useState<EntityKindId>(initialKind);
  const [selected, setSelected] = useState<string | null>(null);
  const [titleState, setTitleState] = useState<SaveState>({ kind: "idle" });
  const [titleDraft, setTitleDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async () => {
    try {
      const p = await projectsApi.get(projectId);
      setProject(p);
      setTitleDraft(p.title);
      setErr(null);
      return p;
    } catch (e) {
      setErr(errorText(e));
      return null;
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const entities = (project?.entities ?? []).filter((e) => e.kind === kind && !e.archived);
  const current = entities.find((e) => e.id === selected) ?? null;

  const saveTitle = async (value: string) => {
    if (!project) return;
    setTitleState({ kind: "saving" });
    try {
      const p = await projectsApi.patch(project.id, { base_revision: project.revision, title: value });
      setProject(p);
      setTitleState({ kind: "saved", at: clock() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setTitleState({ kind: "conflict", message: e.message });
      else if (e instanceof TypeError) setTitleState({ kind: "offline" });
      else setTitleState({ kind: "error", message: errorText(e) });
    }
  };

  const addEntity = async () => {
    if (!project || !newName.trim()) return;
    try {
      const r = await projectsApi.createEntity(project.id, project.revision, kind, newName.trim());
      setProject(r.project);
      setSelected(r.entity_id);
      setNewName("");
      setAdding(false);
    } catch (e) {
      setErr(errorText(e));
    }
  };

  if (err && !project) return <div className="page"><div className="banner bad" role="alert">{err}</div><button className="secondary" onClick={onBack}>Back to projects</button></div>;
  if (!project) return <div className="page"><div className="skeleton" aria-busy="true" style={{ height: 160 }} /></div>;

  return (
    <div className="page wide">
      <button className="ghost" onClick={onBack}>← All projects</button>
      <header className="page-head" style={{ marginTop: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <label className="small muted" htmlFor="ptitle">Project title</label>
          <input
            id="ptitle"
            className="title-input"
            aria-describedby="ptitle-status"
            value={titleDraft}
            maxLength={120}
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={() => { if (titleDraft.trim() && titleDraft !== project.title) void saveTitle(titleDraft.trim()); }}
          />
          <div id="ptitle-status"><SaveStatus state={titleState} onRetry={() => saveTitle(titleDraft.trim())} onReload={() => void load()} /></div>
        </div>
      </header>
      {project.recovery_note && <div className="banner warn" role="status">{project.recovery_note}</div>}

      <Tabs
        label="Library"
        value={kind}
        onChange={(id) => { setKind(id); setSelected(null); }}
        items={KIND_TABS.map((k) => ({
          id: k.id,
          label: (
            <>
              {k.label}
              <span className="tab-count">{(project?.entities ?? []).filter((e) => e.kind === k.id && !e.archived).length}</span>
            </>
          ),
        }))}
      />

      <div className="split" style={{ marginTop: 16 }}>
        <div>
          {!adding && (
            <div className="actions" style={{ marginTop: 0, marginBottom: 10 }}>
              <button onClick={() => setAdding(true)}>Add {KIND_TABS.find((k) => k.id === kind)?.singular}</button>
            </div>
          )}
          {adding && (
            <form className="new-entity" onSubmit={(e) => { e.preventDefault(); void addEntity(); }}>
              <div className="new-entity-head">New {KIND_TABS.find((k) => k.id === kind)?.singular}</div>
              <p className="small muted">Give it a name. Add reference images right after — they are what keep it consistent across takes.</p>
              <label className="small" htmlFor="new-entity-name">Name</label>
              <input id="new-entity-name" aria-label="New item name" maxLength={120} autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} />
              <div className="new-entity-actions">
                <button type="button" className="ghost" onClick={() => { setAdding(false); setNewName(""); }}>Cancel</button>
                <button type="submit" disabled={!newName.trim()}>Create</button>
              </div>
            </form>
          )}
          {entities.length === 0 ? (
            <div className="empty-note">No {KIND_TABS.find((k) => k.id === kind)?.label.toLowerCase()} in this project yet — create one to use it consistently across every take. <strong>Add {KIND_TABS.find((k) => k.id === kind)?.singular}</strong> above to start.</div>
          ) : (
            <ul className="entity-grid" aria-label={`${KIND_TABS.find((k) => k.id === kind)?.label ?? "Items"} list`}>
              {entities.map((e) => {
                const cover = entityCover(project, e);
                return (
                  <li key={e.id}>
                    <button className={`entity-card ${selected === e.id ? "selected" : ""}`} aria-pressed={selected === e.id} onClick={() => setSelected(e.id)}>
                      <span className="entity-cover">
                        {cover ? (
                          <img src={projectsApi.fileUrl(project.id, cover.asset_id)} alt="" loading="lazy" />
                        ) : (
                          <span className="entity-cover-empty">no image yet</span>
                        )}
                        <span className="entity-tag">v{e.current_version}</span>
                      </span>
                      <span className="entity-face"><span className="entity-name">{e.name}</span></span>
                      <span className="entity-chips">
                        <span className="chip">v{e.current_version}</span>
                        <span className="chip">{e.refs.length} {e.refs.length === 1 ? "ref" : "refs"}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <aside className="detail" aria-label="Editor">
          {!current && (
            <div className="empty-hero">
              <h2>Pick something to edit</h2>
              <p>Select a card on the left — or add a new one. Details, references and history for each item are saved to this project as you type.</p>
            </div>
          )}
          {current && (
            <EntityEditor
              key={current.id}
              project={project}
              entity={current}
              onProject={setProject}
              onReloadNeeded={() => void load()}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function EntityEditor({ project, entity, onProject, onReloadNeeded }: {
  project: ProjectView;
  entity: Entity;
  onProject: (p: ProjectView) => void;
  onReloadNeeded: () => void;
}) {
  const [name, setName] = useState(entity.name);
  const [fields, setFields] = useState<Record<string, string>>(entity.fields);
  const [refs, setRefs] = useState(entity.refs);
  const revRef = useRef(entity.revision);      // latest revision the server has confirmed
  const inflight = useRef(false);               // one save at a time
  const queued = useRef<SavePayload | null>(null); // newest edit waiting behind the in-flight save
  const [status, setStatus] = useState<SaveState>({ kind: "idle" });
  const [versions, setVersions] = useState<Entity["versions"] | null>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [role, setRole] = useState(project.ref_roles[entity.kind][0]);
  const timer = useRef<number | undefined>(undefined);
  const labels = project.field_labels[entity.kind];
  const roles = project.ref_roles[entity.kind];

  const save = useCallback(async (payload: SavePayload): Promise<void> => {
    if (inflight.current) { queued.current = payload; return; }  // newest edit wins; sent when the current save ends
    inflight.current = true;
    setStatus({ kind: "saving" });
    let failed = false;
    try {
      const p = await projectsApi.patchEntity(project.id, entity.id, { base_revision: revRef.current, ...payload });
      const updated = p.entities.find((e) => e.id === entity.id);
      if (updated) revRef.current = updated.revision;
      onProject(p);
    } catch (e) {
      failed = true;
      queued.current = null;
      if (e instanceof ApiError && e.status === 409) setStatus({ kind: "conflict", message: e.message });
      else if (e instanceof TypeError) setStatus({ kind: "offline" });
      else setStatus({ kind: "error", message: errorText(e) });
    } finally {
      inflight.current = false;
    }
    if (!failed && queued.current) {
      const next = queued.current;
      queued.current = null;
      await save(next);
    } else if (!failed) {
      setStatus({ kind: "saved", at: clock() });
    }
  }, [project.id, entity.id, onProject]);

  // autosave: debounce edits, then save (queued behind any save already running)
  const scheduleSave = (next: SavePayload) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(next), 800);
  };

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const setField = (k: string, v: string) => {
    const nf = { ...fields, [k]: v };
    setFields(nf);
    scheduleSave({ name, fields: nf, refs });
  };
  const changeName = (v: string) => { setName(v); scheduleSave({ name: v, fields, refs }); };

  const addRef = async (file: File) => {
    setUploadErr(null);
    try {
      const up = await projectsApi.uploadImage(project.id, file, file.name);
      const nr = [...refs, { asset_id: up.asset.id, role }];
      setRefs(nr);
      scheduleSave({ name, fields, refs: nr });
    } catch (e) {
      setUploadErr(errorText(e));
    }
  };

  const removeRef = (aid: string) => {
    const nr = refs.filter((r) => r.asset_id !== aid);
    setRefs(nr);
    scheduleSave({ name, fields, refs: nr });
  };

  const changeRole = (aid: string, newRole: string) => {
    const nr = refs.map((r) => (r.asset_id === aid ? { ...r, role: newRole } : r));
    setRefs(nr);
    scheduleSave({ name, fields, refs: nr });
  };

  const loadVersions = async () => {
    const p = await projectsApi.get(project.id);
    const e = p.entities.find((x) => x.id === entity.id);
    setVersions(e ? e.versions : []);
  };

  const restore = async (v: number) => {
    try {
      const p = await projectsApi.restore(project.id, entity.id, revRef.current, v);
      const e = p.entities.find((x) => x.id === entity.id);
      if (e) { setName(e.name); setFields(e.fields); setRefs(e.refs); revRef.current = e.revision; }
      onProject(p);
      setStatus({ kind: "saved", at: clock() });
      setVersions(e ? e.versions : null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) { setStatus({ kind: "conflict", message: err.message }); }
      else setStatus({ kind: "error", message: errorText(err) });
    }
  };

  const assetById = (id: string) => project.assets.find((a) => a.id === id);

  return (
    <div className="detail-body">
      <div className="comp-head">
        <input aria-label="Name" className="name-input" value={name} maxLength={120} onChange={(e) => changeName(e.target.value)} />
        <SaveStatus state={status} onRetry={() => void save({ name, fields, refs })} onReload={onReloadNeeded} />
      </div>

      <section aria-labelledby="fields-h">
        <h3 id="fields-h">Details</h3>
        {Object.entries(labels).map(([k, label]) => (
          <label key={k} className="field">
            <span className="small">{label}</span>
            {k === "identity_lock" && entity.kind === "character" ? (
              <select aria-label={label} value={fields[k] ?? ""} onChange={(e) => setField(k, e.target.value)}>
                <option value="">Not set</option>
                <option value="off">Off: normal conditioning</option>
                <option value="balanced">Balanced: use identity references where supported</option>
                <option value="strong">Strong: prioritise identity where supported</option>
              </select>
            ) : (
              <textarea rows={k === "description" ? 3 : 2} maxLength={4000} value={fields[k] ?? ""} onChange={(e) => setField(k, e.target.value)} />
            )}
          </label>
        ))}
        {entity.kind === "character" && fields.identity_lock === "strong" && (
          <p className="small warn-text">No workflow in this build applies identity locking yet. The setting is saved with this character and is not applied to generations.</p>
        )}
      </section>

      <section aria-labelledby="refs-h">
        <h3 id="refs-h">Reference images</h3>
        <p className="small muted">Images are copied into this project. Your originals are not changed.</p>
        <div className="ref-add-row">
          <label className="small" style={{ display: "grid", gap: 4, flex: "0 1 auto" }}>Role for the next image
            <select aria-label="Role for next image" value={role} onChange={(e) => setRole(e.target.value)}>
              {roles.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>)}
            </select>
          </label>
          <label className="dropfile">
            <span>Drop or choose an image</span>
            <span className="small muted">PNG, JPEG or WebP · attached as “{ROLE_LABELS[role] ?? role}”</span>
            <input aria-label="Add reference image" type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const f = e.target.files?.[0]; if (f) void addRef(f); e.target.value = ""; }} />
          </label>
        </div>
        {uploadErr && <div className="banner bad" role="alert">{uploadErr}</div>}
        {refs.length === 0 && <p className="small muted">No reference images yet. Add at least one clear image — for characters, a front face shot makes the best cover.</p>}
        {refs.length > 0 && (
          <ul className="ref-tiles">
            {refs.map((r) => {
              const a = assetById(r.asset_id);
              return (
                <li key={r.asset_id} className="ref-tile">
                  {a && a.status === "ok" ? (
                    <img src={projectsApi.fileUrl(project.id, a.id)} alt={a.label || "reference image"} loading="lazy" />
                  ) : (
                    <div className="thumb-missing" role="img" aria-label="image unavailable">unavailable</div>
                  )}
                  <div className="small muted ref-label">{a?.label || r.asset_id}</div>
                  {a && a.status !== "ok" && <div className="small warn-text">{a.status === "missing" ? "File missing from the project folder" : `Changed on disk (${a.status})`}</div>}
                  <select aria-label={`Role for ${a?.label || "image"}`} value={r.role} onChange={(e) => changeRole(r.asset_id, e.target.value)}>
                    {roles.map((x) => <option key={x} value={x}>{ROLE_LABELS[x] ?? x}</option>)}
                  </select>
                  <button className="ghost" onClick={() => removeRef(r.asset_id)}>Remove</button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby="hist-h">
        <h3 id="hist-h">History</h3>
        {versions === null && <button className="secondary" onClick={() => void loadVersions()}>Show versions</button>}
        {versions && (
          <ol className="versions">
            {[...versions].reverse().map((v) => (
              <li key={v.version} className="small">
                <strong>v{v.version}</strong> · {v.note} · {new Date(v.created_at).toLocaleString()}
                {v.version !== entity.current_version && <button className="ghost" onClick={() => void restore(v.version)}>Restore as new version</button>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
