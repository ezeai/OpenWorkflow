import { useCallback, useEffect, useState } from "react";
import { ApiError, continuityApi } from "./api";

type View = Awaited<ReturnType<typeof continuityApi.get>>;

const RULE_TEXT: Record<string, string> = {
  same_character_version: "Same character version in every take",
  reference_take: "Needs an approved take from another shot first",
  world_time: "Keep a location at a set time of day",
};

const PARAM_LABELS: Record<string, [string, string]> = {
  same_character_version: ["Character id", ""],
  reference_take: ["Target shot id", "Source shot id"],
  world_time: ["Location id", "Time of day (e.g. night)"],
};

export function ContinuityPanel({ projectId }: { projectId: string }) {
  const [view, setView] = useState<View | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [kind, setKind] = useState("same_character_version");
  const [param1, setParam1] = useState("");
  const [param2, setParam2] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(() => continuityApi.get(projectId).then(setView).catch((e: Error) => setErr(e.message)), [projectId]);
  useEffect(() => { void load(); }, [load]);

  const act = async (fn: (rev: number) => Promise<unknown>) => {
    if (!view) return;
    setErr(null);
    try {
      await fn(view.revision);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? "Changed elsewhere. Reloaded." : e instanceof Error ? e.message : "Failed.");
    }
    await load();
  };

  if (!view) return <div className="skeleton" aria-busy="true" style={{ height: 120 }} />;

  const [p1Label, p2Label] = PARAM_LABELS[kind] ?? ["Id", ""];
  const paramsFor = (): Record<string, string> => {
    if (kind === "same_character_version") return { entity_id: param1.trim() };
    if (kind === "reference_take") return { target_shot: param1.trim(), source_shot: param2.trim() };
    return { world_id: param1.trim(), time_of_day: param2.trim() };
  };

  return (
    <section className="card" aria-labelledby="cont-h">
      <h2 id="cont-h">Continuity</h2>
      <p className="small muted">Checks run whenever the project is read. A warning never blocks work. You can accept it with a reason, and the reason stays visible.</p>
      {err && <div className="banner bad" role="alert">{err}</div>}

      {view.warnings.length === 0 && <p className="muted">No continuity warnings.</p>}
      <ul className="warnings-list">
        {view.warnings.map((w) => (
          <li key={w.key} className={`cont-item ${w.accepted ? "accepted" : ""}`}>
            <div>{w.message} {w.accepted && <span className="pill info">Accepted</span>}</div>
            <div className="small muted">{w.why}</div>
            {w.accepted ? (
              <div className="small">Reason: {w.accepted_reason}</div>
            ) : (
              <div className="inline-form">
                <label className="small">Reason for accepting
                  <input aria-label={`Reason for ${w.message}`} maxLength={500} value={reasons[w.key] ?? ""}
                    onChange={(e) => setReasons({ ...reasons, [w.key]: e.target.value })} />
                </label>
                <button className="secondary" disabled={(reasons[w.key] ?? "").trim().length < 3}
                  onClick={() => act((rev) => continuityApi.accept(projectId, rev, w.key, (reasons[w.key] ?? "").trim()))}>
                  Accept with reason
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      <h3>Rules</h3>
      {view.rules.length === 0 && <p className="muted small">No rules yet.</p>}
      <ul className="small">
        {view.rules.map((r) => (
          <li key={r.id}>
            <strong>{RULE_TEXT[r.kind] ?? r.kind}</strong> <span className="muted">{Object.values(r.params).join(" · ")}</span>
            {r.note && <span className="muted"> · {r.note}</span>}
            <button className="ghost" onClick={() => act((rev) => continuityApi.removeRule(projectId, r.id, rev))}>Remove</button>
          </li>
        ))}
      </ul>
      <form className="inline-form" onSubmit={(e) => {
        e.preventDefault();
        void act((rev) => continuityApi.addRule(projectId, rev, kind, paramsFor(), note));
        setParam1(""); setParam2(""); setNote("");
      }}>
        <label className="small">Rule
          <select aria-label="Rule kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {view.rule_kinds.map((k) => <option key={k} value={k}>{RULE_TEXT[k] ?? k}</option>)}
          </select>
        </label>
        <label className="small">{p1Label}
          <input aria-label={p1Label} value={param1} onChange={(e) => setParam1(e.target.value)} />
        </label>
        {p2Label && (
          <label className="small">{p2Label}
            <input aria-label={p2Label} value={param2} onChange={(e) => setParam2(e.target.value)} />
          </label>
        )}
        <label className="small">Note
          <input aria-label="Rule note" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button type="submit" disabled={!param1.trim() || (!!p2Label && !param2.trim())}>Add rule</button>
      </form>
      <p className="small muted">Ids appear in the Characters, Worlds and Shots views. Paste them here.</p>
    </section>
  );
}
