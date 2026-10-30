import { useState } from "react";
import { navTabsApi, type ForgeWorkflow, type NavTab } from "./api";

// Editor for the imported apps pinned to the main navigation: rename, reorder, remove, then save.

export const NAV_TAB_LABEL_MAX = 40;

export function tabLabelFor(app: ForgeWorkflow): string {
  return app.name.trim().slice(0, NAV_TAB_LABEL_MAX) || "App";
}

export function NavTabsEditor({ tabs, apps, onSaved }: { tabs: NavTab[]; apps: ForgeWorkflow[]; onSaved: (tabs: NavTab[]) => void }) {
  const [draft, setDraft] = useState<NavTab[]>(tabs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const names = new Map(apps.map((a) => [a.id, a.name]));

  const move = (i: number, d: number) => setDraft((all) => {
    const j = i + d;
    if (j < 0 || j >= all.length) return all;
    const next = [...all];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });

  const save = async () => {
    setBusy(true); setError(null);
    try {
      const r = await navTabsApi.save(draft.map((t) => ({ app_id: t.app_id, label: t.label.trim() })));
      onSaved(r.tabs);
      setDraft(r.tabs);
    } catch (e) { setError(e instanceof Error ? e.message : "The nav tabs could not be saved."); }
    finally { setBusy(false); }
  };

  const dirty = JSON.stringify(draft) !== JSON.stringify(tabs);
  const blank = draft.some((t) => !t.label.trim());

  return (
    <section className="card" aria-labelledby="navtabs-h">
      <h3 id="navtabs-h">Nav tabs</h3>
      <p className="muted">Imported apps shown in the main navigation. Rename a tab, change its order, or remove it. The apps themselves are not changed.</p>
      {draft.length === 0 && <p className="muted">No tabs yet. Use "Pin to nav" on an app.</p>}
      <ol className="plain">
        {draft.map((t, i) => (
          <li key={t.app_id} className="row">
            <input aria-label={`Tab name for ${names.get(t.app_id) ?? "an app"}`} maxLength={NAV_TAB_LABEL_MAX} value={t.label}
              onChange={(e) => setDraft((all) => all.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} disabled={busy} />
            <span className="muted small">{names.get(t.app_id) ?? "App no longer imported"}</span>
            <button className="secondary" onClick={() => move(i, -1)} disabled={busy || i === 0} aria-label={`Move ${t.label} up`}>Up</button>
            <button className="secondary" onClick={() => move(i, 1)} disabled={busy || i === draft.length - 1} aria-label={`Move ${t.label} down`}>Down</button>
            <button className="secondary" onClick={() => setDraft((all) => all.filter((_, j) => j !== i))} disabled={busy} aria-label={`Remove ${t.label}`}>Remove</button>
          </li>
        ))}
      </ol>
      {error && <div className="banner bad" role="alert">{error}</div>}
      {blank && <p className="muted" role="status">Every tab needs a name.</p>}
      <div className="actions">
        <button onClick={() => void save()} disabled={busy || !dirty || blank}>{busy ? "Saving…" : "Save nav tabs"}</button>
        <button className="secondary" onClick={() => setDraft(tabs)} disabled={busy || !dirty}>Undo changes</button>
      </div>
    </section>
  );
}
