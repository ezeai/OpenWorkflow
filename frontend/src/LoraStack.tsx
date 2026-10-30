import { useEffect, useState } from "react";

type Lora = { name: string; strength: number; enabled: boolean };
type Choice = { file: string };

const MIN = -2, MAX = 2, MAX_ITEMS = 8;

/** The app's LoRA stack: pick LoRA files ComfyUI has, set strengths, and save. The workflow file itself is not changed. */
export function LoraStack({ workflowId }: { workflowId: string }) {
  const [items, setItems] = useState<Lora[]>([]);
  const [saved, setSaved] = useState<Lora[]>([]);
  const [choices, setChoices] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/forge/workflows/${workflowId}/loras`).then((r) => r.json()).then((d: { items: Lora[] }) => {
      setItems(d.items); setSaved(d.items);
    }).catch(() => setErr("The LoRA stack could not be read."));
    fetch("/api/forge/models?folder=loras").then((r) => r.json()).then((d: { items: Choice[] }) => setChoices(d.items.map((c) => c.file))).catch(() => undefined);
  }, [workflowId]);

  const dirty = JSON.stringify(items) !== JSON.stringify(saved);
  const setRow = (i: number, patch: Partial<Lora>) => setItems((cur) => cur.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = async () => {
    setErr(null); setNote(null);
    const r = await fetch(`/api/forge/workflows/${workflowId}/loras`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }),
    });
    const body = await r.json().catch(() => null);
    if (!r.ok) { setErr(body?.detail ?? "The stack could not be saved."); return; }
    setSaved(body.items); setItems(body.items); setNote("Saved. The next run uses this stack.");
  };

  return (
    <section className="lora-stack" aria-labelledby="lora-h">
      <h3 id="lora-h" className="sub-h">LoRA stack</h3>
      {items.length === 0 && <p className="small muted">No LoRAs. Add one to change the look of this app without editing the workflow.</p>}
      <ol className="lora-rows">
        {items.map((it, i) => (
          <li key={i} className="lora-row">
            <select aria-label={`LoRA ${i + 1} file`} value={it.name} onChange={(e) => setRow(i, { name: e.target.value })}>
              {!choices.includes(it.name) && <option value={it.name}>{it.name} (not found)</option>}
              {choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <label className="small">Strength
              <input type="number" min={MIN} max={MAX} step={0.05} value={it.strength}
                onChange={(e) => setRow(i, { strength: Math.min(MAX, Math.max(MIN, Number(e.target.value) || 0)) })} />
            </label>
            <label className="check"><input type="checkbox" checked={it.enabled} onChange={(e) => setRow(i, { enabled: e.target.checked })} /> On</label>
            <button type="button" className="ghost small-btn" onClick={() => setItems((cur) => cur.filter((_, j) => j !== i))} aria-label={`Remove LoRA ${i + 1}`}>Remove</button>
          </li>
        ))}
      </ol>
      <div className="run-actions">
        <button type="button" className="secondary small-btn" disabled={items.length >= MAX_ITEMS || choices.length === 0}
          onClick={() => setItems((cur) => [...cur, { name: choices[0] ?? "", strength: 1, enabled: true }])}>Add a LoRA</button>
        <button type="button" className="small-btn" disabled={!dirty} onClick={() => void save()}>Save stack</button>
      </div>
      {choices.length === 0 && <p className="small muted">ComfyUI's LoRA folder is empty or not reachable. Start ComfyUI to choose LoRAs.</p>}
      {err && <p className="small warn-text" role="alert">{err}</p>}
      {note && <p className="small muted" role="status">{note}</p>}
    </section>
  );
}
