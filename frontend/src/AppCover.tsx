import { useRef, useState } from "react";

export const coverUrl = (id: string, v = 0) => `/api/forge/workflows/${id}/cover${v ? `?v=${v}` : ""}`;

/** Up to two letters from the app's name, for the placeholder when there is no picture yet. */
export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

/** The app's picture on its library card. Without one, a placeholder with the app's initials on its colour. */
export function AppCover({ id, tone, name = "", tag, version = 0 }: { id: string; tone: string; name?: string; tag?: string; version?: number }) {
  const [missing, setMissing] = useState(false);
  return (
    <span className={`lib-thumb${missing ? " no-cover" : ""}`} style={{ background: tone }} aria-hidden="true">
      {!missing && <img className="lib-cover" src={coverUrl(id, version)} alt="" loading="lazy" onError={() => setMissing(true)} />}
      {missing && <span className="lib-initials">{initials(name)}</span>}
      {tag && <span className="lib-tag">{tag}</span>}
    </span>
  );
}

const TYPES = ["image/png", "image/jpeg", "image/webp"];

/** Set or remove an app's picture. The file is checked by the server; only PNG, JPEG and WebP are accepted. */
export function CoverPicker({ id, onChanged, compact = false }: { id: string; hasCover?: boolean; onChanged: () => void; compact?: boolean }) {
  const input = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const upload = async (file: File) => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/forge/workflows/${id}/cover`, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "The picture could not be saved.");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The picture could not be saved.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const clear = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/forge/workflows/${id}/cover`, { method: "DELETE" });
      if (!r.ok) throw new Error("The picture could not be removed.");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The picture could not be removed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cover-picker">
      {!compact && <span className="small muted">The picture shows on this app's card. Without one, the card shows the output of its latest run.</span>}
      <div className="run-actions">
        <button type="button" className="secondary small-btn" onClick={() => input.current?.click()} disabled={busy}>
          {busy ? "Saving…" : "Card picture"}
        </button>
        <button type="button" className="ghost small-btn" onClick={() => void clear()} disabled={busy}>Remove picture</button>
      </div>
      <input ref={input} type="file" accept={TYPES.join(",")} hidden aria-label="Choose a picture for this app"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
      {err && <p className="small warn-text" role="alert">{err}</p>}
    </div>
  );
}
