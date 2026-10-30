import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { PosterBox } from "./api";

export type BoxTool = "obj" | "text";

const MAX_BOXES = 24;
const MIN_SIZE = 0.03;          // a drag smaller than this is a click, not a box
const clamp = (v: number) => Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
const near = (v: number) => Math.round(v * 100) / 100;
// Content Studio rule of thumb: about 60 capitals fill the full width of a line, scaled by box height.
const charBudget = (b: PosterBox) => Math.max(3, Math.round(b.w * 60 * Math.min(1, b.h * 12)));
const HEX_CODE = /^#?[0-9a-fA-F]{6}$/;

type Drag =
  | { kind: "draw"; i: number; start: { x: number; y: number } }
  | { kind: "move"; i: number; start: { x: number; y: number }; orig: PosterBox }
  | { kind: "resize"; i: number; orig: PosterBox };

/**
 * A picture-sized stage where boxes are drawn, moved and resized, plus an inspector for the selected box.
 * Drag on empty space to draw, drag a box to move it, drag its corner to resize. Delete removes the selected box,
 * Escape clears the selection, and the arrow keys nudge it (Shift for bigger steps).
 */
export function LayoutEditor({ boxes, onChange, warnings, tool, onTool, ratio }: {
  boxes: PosterBox[]; onChange: (boxes: PosterBox[]) => void; warnings: string[][]; tool: BoxTool; onTool: (t: BoxTool) => void;
  ratio: number;  // width divided by height of the poster type, so the stage has the right shape
}) {
  const [sel, setSel] = useState<number | null>(null);
  const [colourDraft, setColourDraft] = useState("");
  const [colourErr, setColourErr] = useState<string | null>(null);
  // Refresh the colour text only when the selection changes, so typing is not overwritten.
  useEffect(() => {
    const b = sel === null ? undefined : boxes[sel];
    setColourDraft((b?.palette ?? []).join(", "));
    setColourErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel]);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<Drag | null>(null);
  const latest = useRef(boxes);
  latest.current = boxes;   // pointer moves read the newest boxes, not a stale copy

  const toFrac = (e: PointerEvent) => {
    const r = stageRef.current?.getBoundingClientRect();
    if (!r || r.width === 0 || r.height === 0) return null;
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  };
  const put = (next: PosterBox[]) => onChange(next);
  const patchAt = (i: number, patch: Partial<PosterBox>) =>
    put(latest.current.map((b, j) => (j === i ? { ...b, ...patch } : b)));

  const onStageDown = (e: PointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const boxEl = target.closest<HTMLElement>("[data-box]");
    const p = toFrac(e);
    if (!p) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    if (boxEl) {
      const i = Number(boxEl.dataset.box);
      setSel(i);
      const orig = latest.current[i];
      drag.current = target.dataset.handle ? { kind: "resize", i, orig } : { kind: "move", i, start: p, orig };
      return;
    }
    if (latest.current.length >= MAX_BOXES) return;
    const next = [...latest.current, { type: tool, x: p.x, y: p.y, w: 0, h: 0, desc: "", text: "" }];
    put(next);
    setSel(next.length - 1);
    drag.current = { kind: "draw", i: next.length - 1, start: p };
  };

  const onStageMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const p = toFrac(e);
    if (!d || !p) return;
    if (d.kind === "draw") {
      const x = Math.min(d.start.x, p.x), y = Math.min(d.start.y, p.y);
      patchAt(d.i, { x: near(x), y: near(y), w: near(Math.abs(p.x - d.start.x)), h: near(Math.abs(p.y - d.start.y)) });
    } else if (d.kind === "move") {
      const dx = p.x - d.start.x, dy = p.y - d.start.y;
      const x = clamp(Math.min(d.orig.x + dx, 1 - d.orig.w)), y = clamp(Math.min(d.orig.y + dy, 1 - d.orig.h));
      patchAt(d.i, { x: near(x), y: near(y) });
    } else {
      const b = latest.current[d.i];
      patchAt(d.i, { w: near(Math.max(MIN_SIZE, Math.min(p.x - b.x, 1 - b.x))), h: near(Math.max(MIN_SIZE, Math.min(p.y - b.y, 1 - b.y))) });
    }
  };

  const onStageUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "draw") {
      const b = latest.current[d.i];
      if (b && (b.w < MIN_SIZE || b.h < MIN_SIZE)) {   // a click, not a drag: drop the empty box
        put(latest.current.filter((_, j) => j !== d.i));
        setSel(null);
      }
    }
  };

  const remove = (i: number) => { put(latest.current.filter((_, j) => j !== i)); setSel(null); };
  const duplicate = (i: number) => {
    if (latest.current.length >= MAX_BOXES) return;
    const b = latest.current[i];
    const next = [...latest.current.slice(0, i + 1), { ...b, x: clamp(b.x + 0.03), y: clamp(b.y + 0.03) }, ...latest.current.slice(i + 1)];
    put(next);
    setSel(i + 1);
  };
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= latest.current.length) return;
    const next = [...latest.current];
    [next[i], next[j]] = [next[j], next[i]];
    put(next);
    setSel(j);
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (sel === null) return;
    const b = latest.current[sel];
    if (!b) return;
    const step = e.shiftKey ? 0.05 : 0.01;
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); remove(sel); }
    else if (e.key === "Escape") setSel(null);
    else if (e.key === "ArrowLeft") { e.preventDefault(); patchAt(sel, { x: near(clamp(b.x - step)) }); }
    else if (e.key === "ArrowRight") { e.preventDefault(); patchAt(sel, { x: near(clamp(Math.min(b.x + step, 1 - b.w))) }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); patchAt(sel, { y: near(clamp(b.y - step)) }); }
    else if (e.key === "ArrowDown") { e.preventDefault(); patchAt(sel, { y: near(clamp(Math.min(b.y + step, 1 - b.h))) }); }
  };

  const selected = sel === null ? null : boxes[sel] ?? null;
  const flagged = warnings.map((w, i) => ({ i, w })).filter((x) => x.w.length > 0);

  return (
    <div className="box-editor">
      <div className="box-tools" role="toolbar" aria-label="Box tools">
        <div className="lt-seg" role="group" aria-label="What to draw next">
          <button type="button" aria-pressed={tool === "obj"} onClick={() => onTool("obj")}>Product box</button>
          <button type="button" aria-pressed={tool === "text"} onClick={() => onTool("text")}>Text box</button>
        </div>
        <span className="small muted">Drag on the picture to draw. Drag a box to move it, its corner to resize.</span>
      </div>

      <div ref={stageRef} className="layout-canvas editor-stage" tabIndex={0} role="application" style={{ aspectRatio: String(ratio) }}
        aria-label={`Layout canvas with ${boxes.length} boxes. Delete removes the selected box, Escape clears the selection, arrow keys move it.`}
        onPointerDown={onStageDown} onPointerMove={onStageMove} onPointerUp={onStageUp} onPointerCancel={onStageUp}
        onKeyDown={onKey}>
        {boxes.map((b, i) => (
          <div key={i} data-box={i} className={`layout-box ${b.type}${sel === i ? " selected" : ""}${warnings[i]?.length ? " flagged" : ""}`}
            style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }}>
            <span className="box-chip">{i + 1}{b.type === "text" ? " · text" : ""}{warnings[i]?.length ? " (check)" : ""}</span>
            {b.type === "text" && b.text && <span className="box-words">{b.text}</span>}
            <span className="box-handle" data-handle="1" aria-hidden="true" />
          </div>
        ))}
        {boxes.length === 0 && <p className="stage-empty">Nothing placed yet. Drag here to draw the product, then a text box for the headline.</p>}
      </div>

      {selected && sel !== null && (
        <div className="box-inspector" aria-label={`Box ${sel + 1}`}>
          <div className="box-head">
            <strong>Box {sel + 1}</strong>
            <span className="small muted">{selected.type === "text" ? "exact text" : "product or object"}</span>
          </div>
          <div className="lt-seg" role="group" aria-label="Box type">
            <button type="button" aria-pressed={selected.type !== "text"} onClick={() => patchAt(sel, { type: "obj" })}>Product</button>
            <button type="button" aria-pressed={selected.type === "text"} onClick={() => patchAt(sel, { type: "text" })}>Text</button>
          </div>
          <label className="r-field"><span>Describe what goes here</span>
            <input type="text" value={selected.desc ?? ""} onChange={(e) => patchAt(sel, { desc: e.target.value })} /></label>
          {selected.type === "text" && (
            <label className="r-field"><span>Words to render exactly</span>
              <input type="text" maxLength={200} value={selected.text ?? ""} onChange={(e) => patchAt(sel, { text: e.target.value })} placeholder="SUMMER SALE" /></label>
          )}
          {selected.type === "text" && (
              <p className="small muted">About {charBudget(selected)} characters per line. {(selected.text ?? "").trim().split(/\s+/).filter(Boolean).length} of 5 words. Capitals read best.</p>
            )}
            <label className="r-field"><span>Colours (optional hex codes, comma separated)</span>
              <input type="text" value={colourDraft} placeholder="#C8102E, #F5F1E6"
                onChange={(e) => {
                  setColourDraft(e.target.value);
                  const parts = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                  if (parts.every((c) => HEX_CODE.test(c)) && parts.length <= 5) {
                    setColourErr(null);
                    patchAt(sel, { palette: parts.map((c) => ("#" + c.replace(/^#/, "")).toUpperCase()) });
                  } else {
                    setColourErr("Use up to five colour codes like #C8102E.");
                  }
                }} /></label>
            {colourErr && <p className="small warn-text" role="status">{colourErr}</p>}
          <div className="box-nums">
            {(["x", "y", "w", "h"] as const).map((k) => (
              <label key={k} className="small">{k === "x" ? "Left" : k === "y" ? "Top" : k === "w" ? "Width" : "Height"}
                <input type="number" min={0} max={1} step={0.01} value={Number(selected[k]).toFixed(2)}
                  onChange={(e) => patchAt(sel, { [k]: clamp(Number(e.target.value)) })} /></label>
            ))}
          </div>
          <div className="preset-row">
            <button type="button" className="secondary small-btn" onClick={() => move(sel, -1)} disabled={sel === 0}>Move earlier</button>
            <button type="button" className="secondary small-btn" onClick={() => move(sel, 1)} disabled={sel === boxes.length - 1}>Move later</button>
            <button type="button" className="secondary small-btn" onClick={() => duplicate(sel)} disabled={boxes.length >= MAX_BOXES}>Duplicate</button>
            <button type="button" className="ghost small-btn" onClick={() => remove(sel)}>Delete</button>
          </div>
        </div>
      )}

      {flagged.length > 0 && (
        <ul className="box-warnings" aria-label="Things to check before generating">
          {flagged.map(({ i, w }) => w.map((m) => <li key={`${i}-${m}`}><strong>Box {i + 1}:</strong> {m}</li>))}
        </ul>
      )}
    </div>
  );
}
