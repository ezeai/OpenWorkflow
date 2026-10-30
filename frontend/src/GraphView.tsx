import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import "./GraphView.css";

export type Param = {
  name: string; type?: string; value: string | number | boolean | null;
  min?: number; max?: number; step?: number; default?: string | number | boolean | null; choices?: (string | number)[];
};
export type Slot = { name: string; type: string; widget?: boolean };
export type GNode = { id: string; type: string; title: string; state: string; ui_only: boolean; group: string | null; scope?: string | null; known?: boolean; params: Param[]; inputs?: Slot[]; outputs?: Slot[]; pos: [number, number] | null; size: [number, number] | null; added?: boolean };
type TypeHit = { type: string; category: string; inputs: { name: string; type: string }[]; widgets: string[]; outputs: string[]; output_node: boolean };
type Category = { name: string; count: number };
export type GLink = { id: string; from: string; from_slot: number; to: string; to_slot: number; type: string };
export type SubgraphDef = { id: string; name: string; opened: number; nodes: number; inputs: string[]; outputs: string[] };
/** One opened subgraph box. Its nodes carry `scope` equal to the box's `scope`; `parent` is the box it sat in. */
export type SubgraphBox = { scope: string; parent: string | null; name: string; label: string };
export type Graph = { format: string; nodes: GNode[]; links: GLink[]; subgraphs?: SubgraphDef[]; boxes?: SubgraphBox[]; note?: string };
const STATE_LABEL: Record<string, string> = { active: "Active", muted: "Muted", bypassed: "Bypassed" };

/** A small overview of the whole view. The outlined rectangle is what the canvas is showing; a click moves there. */
function Minimap({ bounds, boxes, view, canvas, selected, onJump }: {
  bounds: Rect; boxes: Map<string, Box>; view: { zoom: number; x: number; y: number }; canvas: HTMLElement | null;
  selected: string | null; onJump: (wx: number, wy: number) => void;
}) {
  const W = 160;
  const H = 100;
  const scale = Math.min(W / Math.max(bounds.w, 1), H / Math.max(bounds.h, 1));
  const cw = canvas?.clientWidth || 800;
  const ch = canvas?.clientHeight || 460;
  const vx = -view.x / view.zoom;
  const vy = -view.y / view.zoom;
  return (
    <div
      className="gv-minimap"
      role="img"
      aria-label="Overview of the canvas. Click to move the view."
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        onJump(bounds.x + (e.clientX - r.left) / scale, bounds.y + (e.clientY - r.top) / scale);
      }}
    >
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        {[...boxes.entries()].map(([id, b]) => (
          <rect key={id} x={(b.x - bounds.x) * scale} y={(b.y - bounds.y) * scale} width={Math.max(2, b.w * scale)} height={Math.max(2, b.h * scale)}
            className={selected === id ? "mm-node sel" : "mm-node"} />
        ))}
        <rect className="mm-view" x={(vx - bounds.x) * scale} y={(vy - bounds.y) * scale} width={(cw / view.zoom) * scale} height={(ch / view.zoom) * scale} />
      </svg>
    </div>
  );
}

/** Every node that feeds `id` (up) and every node `id` feeds (down), following links through the whole workflow. */
export function traceOf(g: Graph, id: string): { up: Set<string>; down: Set<string> } {
  const inbound = new Map<string, string[]>();
  const outbound = new Map<string, string[]>();
  for (const l of g.links) {
    inbound.set(l.to, [...(inbound.get(l.to) ?? []), l.from]);
    outbound.set(l.from, [...(outbound.get(l.from) ?? []), l.to]);
  }
  const walk = (edges: Map<string, string[]>) => {
    const seen = new Set<string>();
    const stack = [id];
    while (stack.length) {
      const at = stack.pop()!;
      for (const next of edges.get(at) ?? []) {
        if (next !== id && !seen.has(next)) { seen.add(next); stack.push(next); }
      }
    }
    return seen;
  };
  return { up: walk(inbound), down: walk(outbound) };
}

/** The part of the workflow inside one subgraph box, nested boxes included. Links that cross the box edge are not shown. */
export function focusView(g: Graph, focus: string | null): Graph {
  if (!focus) return g;
  const inside = (s: string | null | undefined) => !!s && (s === focus || s.startsWith(`${focus}:`));
  const nodes = g.nodes.filter((n) => inside(n.scope));
  const ids = new Set(nodes.map((n) => n.id));
  return { ...g, nodes, links: g.links.filter((l) => ids.has(l.from) && ids.has(l.to)) };
}

/** The boxes from the outside in, ending at `focus`, for the breadcrumb. */
export function trail(boxes: SubgraphBox[], focus: string | null): SubgraphBox[] {
  const out: SubgraphBox[] = [];
  let at = focus;
  while (at) {
    const b = boxes.find((x) => x.scope === at);
    if (!b) break;
    out.unshift(b);
    at = b.parent;
  }
  return out;
}

/** The limits ComfyUI states for a setting, as one short line. */
export function limitsText(p: Param): string {
  const parts: string[] = [];
  if (p.choices?.length) parts.push(`one of ${p.choices.length}: ${p.choices.slice(0, 6).join(", ")}${p.choices.length > 6 ? " …" : ""}`);
  if (p.min !== undefined || p.max !== undefined) parts.push(`${p.min ?? "−∞"} to ${p.max ?? "∞"}`);
  if (p.step !== undefined) parts.push(`step ${p.step}`);
  if (p.default !== undefined && p.default !== null) parts.push(`default ${String(p.default)}`);
  return parts.join(" · ");
}
export type Rect = { x: number; y: number; w: number; h: number };
export type Box = Rect & { head: number };   // head: the height of the title area, which depends on the node

const HEAD = 30;        // title bar
const GROUP_LINE = 16;  // "in <subgraph>" under the title, for nodes opened from a subgraph
const PARAM_LINE = 16;  // one setting on the card
const CARD_PARAMS = 4;  // the card shows this many settings; the details panel shows them all
const ROW = 20;         // one slot row
const MIN_W = 180;
const MAX_W = 360;
const DEFAULT_W = 210;
const GAP = 28;
const SUBGRAPH = /^[0-9a-f]{8}-[0-9a-f]{4}-/i;   // subgraph nodes are named by a UUID

export const TYPE_COLOR: Record<string, string> = {
  IMAGE: "#4da3ff", LATENT: "#c77dff", MODEL: "#8ecf5e", CLIP: "#f2c94c", CONDITIONING: "#ff8c42",
  VAE: "#ff5d5d", MASK: "#5fd3c2", CONTROL_NET: "#e879b9", AUDIO: "#9aa7ff",
};
const LINK_DEFAULT = "#8a8f98";

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** How many input and output slots each node uses, counted from the highest slot number that has a link. */
export function slotCounts(g: Graph): { ins: Map<string, number>; outs: Map<string, number> } {
  const ins = new Map<string, number>();
  const outs = new Map<string, number>();
  for (const l of g.links) {
    ins.set(l.to, Math.max(ins.get(l.to) ?? 0, l.to_slot + 1));
    outs.set(l.from, Math.max(outs.get(l.from) ?? 0, l.from_slot + 1));
  }
  return { ins, outs };
}

/** The longest chain of links leading into each node, so sources sit left and results sit right. A cycle is cut, not followed. */
export function depths(g: Graph): Map<string, number> {
  const inbound = new Map<string, string[]>();
  for (const l of g.links) inbound.set(l.to, [...(inbound.get(l.to) ?? []), l.from]);
  const memo = new Map<string, number>();
  const visit = (id: string, stack: Set<string>): number => {
    if (memo.has(id)) return memo.get(id)!;
    if (stack.has(id)) return 0;
    stack.add(id);
    const d = Math.max(-1, ...(inbound.get(id) ?? []).map((p) => visit(p, stack))) + 1;
    stack.delete(id);
    memo.set(id, d);
    return d;
  };
  for (const n of g.nodes) visit(n.id, new Set());
  return memo;
}

/** Rows on a card: one per socket, in the editor's numbering, so a link lands on the row of its own slot. */
export function rowCount(n: GNode, slots: { ins: Map<string, number>; outs: Map<string, number> }): number {
  const lastLinkable = (n.inputs ?? []).reduce((m, s, i) => (s.widget ? m : i + 1), 0);
  return Math.max(1, slots.ins.get(n.id) ?? 0, slots.outs.get(n.id) ?? 0, lastLinkable, n.outputs?.length ?? 0);
}

function sizeOf(n: GNode, slots: { ins: Map<string, number>; outs: Map<string, number> }): { w: number; h: number; head: number } {
  const w = n.size ? clamp(n.size[0], MIN_W, MAX_W) : DEFAULT_W;
  const rows = rowCount(n, slots);
  const head = HEAD + (n.group ? GROUP_LINE : 0);
  const params = Math.min(n.params.length, CARD_PARAMS) * PARAM_LINE + (n.params.length ? 6 : 0);
  return { w, h: head + 8 + rows * ROW + params + 8, head };
}

/** Where each node is drawn. Uses the editor's own positions when every node has one; otherwise lays them out by depth. */
export function layout(g: Graph): { boxes: Map<string, Box>; bounds: Rect } {
  const slots = slotCounts(g);
  const boxes = new Map<string, Box>();
  if (g.nodes.length > 0 && g.nodes.every((n) => n.pos)) {
    for (const n of g.nodes) {
      const s = sizeOf(n, slots);
      boxes.set(n.id, { x: n.pos![0], y: n.pos![1], ...s });
    }
  } else {
    const d = depths(g);
    const cols = new Map<number, GNode[]>();
    for (const n of g.nodes) cols.set(d.get(n.id) ?? 0, [...(cols.get(d.get(n.id) ?? 0) ?? []), n]);
    let x = 0;
    for (const c of [...cols.keys()].sort((a, b) => a - b)) {
      let y = 0;
      let colW = 0;
      for (const n of cols.get(c)!) {
        const s = sizeOf(n, slots);
        boxes.set(n.id, { x, y, ...s });
        y += s.h + GAP;
        colW = Math.max(colW, s.w);
      }
      x += colW + GAP * 3;
    }
  }
  const all = [...boxes.values()];
  const minX = Math.min(0, ...all.map((b) => b.x));
  const minY = Math.min(0, ...all.map((b) => b.y));
  const maxX = Math.max(0, ...all.map((b) => b.x + b.w));
  const maxY = Math.max(0, ...all.map((b) => b.y + b.h));
  return { boxes, bounds: { x: minX, y: minY, w: maxX - minX, h: maxY - minY } };
}

/** The point on a node's edge where a slot's link attaches. */
function slotPoint(b: Box, slot: number, side: "in" | "out"): [number, number] {
  const y = b.y + b.head + 8 + slot * ROW + ROW / 2;
  return [side === "out" ? b.x + b.w : b.x, y];
}

function curve(a: [number, number], b: [number, number]): string {
  const k = Math.max(40, Math.abs(b[0] - a[0]) * 0.5);
  return `M ${a[0]} ${a[1]} C ${a[0] + k} ${a[1]}, ${b[0] - k} ${b[1]}, ${b[0]} ${b[1]}`;
}

type View = { zoom: number; x: number; y: number };
type Drag = { kind: "pan" | "node"; id?: string; sx: number; sy: number; ox: number; oy: number; moved: boolean };

/** The workflow's nodes and links drawn on a canvas. Pan by dragging the background, zoom with the wheel,
 *  move a node by dragging it, and select a node to see what it connects to. Moves are not saved: the graph is read only. */
export function GraphView({ workflowId }: { workflowId: string }) {
  const [g, setG] = useState<Graph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>({ zoom: 1, x: 0, y: 0 });
  const [moved, setMoved] = useState<Record<string, [number, number]>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);   // the subgraph box being viewed; null is the whole workflow
  const [copied, setCopied] = useState<string | null>(null);
  const [lens, setLens] = useState(false);                                  // highlight the nodes the app exposes as controls
  const [controls, setControls] = useState<{ id: string; label: string; nodeId: string }[] | null>(null);
  const [trace, setTrace] = useState(true);                                 // highlight what feeds the selected node, and what it feeds
  const [renaming, setRenaming] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [opErr, setOpErr] = useState<string | null>(null);
  const [changes, setChanges] = useState(0);
  const [find, setFind] = useState("");
  const findRef = useRef<HTMLInputElement | null>(null);

  // Keys work anywhere on the page except while typing in a field.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
        if (e.key === "Escape") (t as HTMLInputElement).blur();
        return;
      }
      if (e.key === "+" || e.key === "=") setView((v) => ({ ...v, zoom: clamp(v.zoom * 1.2, 0.2, 2.5) }));
      else if (e.key === "-") setView((v) => ({ ...v, zoom: clamp(v.zoom / 1.2, 0.2, 2.5) }));
      else if (e.key.toLowerCase() === "f" && lay) fit(canvas.current, lay.bounds);
      else if (e.key === "/") { e.preventDefault(); findRef.current?.focus(); }
      else if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const canvas = useRef<HTMLDivElement | null>(null);
  const drag = useRef<Drag | null>(null);
  const justDragged = useRef(false);
  const [adding, setAdding] = useState(false);
  const [typeQ, setTypeQ] = useState("");
  const [hits, setHits] = useState<TypeHit[]>([]);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [cats, setCats] = useState<Category[]>([]);
  const [picked, setPicked] = useState<TypeHit | null>(null);

  // The add-node picker: ComfyUI's own node types, filtered by search text and category. A short pause while typing.
  useEffect(() => {
    if (!adding) return;
    let alive = true;
    const t = window.setTimeout(() => {
      const params = new URLSearchParams({ q: typeQ, limit: "40" });
      if (category) params.set("category", category);
      fetch(`/api/forge/node-types?${params}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("The node list could not be read."))))
        .then((d: { items: TypeHit[] }) => { if (alive) setHits(d.items); })
        .catch((e) => { if (alive) setAddErr(e instanceof Error ? e.message : "The node list could not be read."); });
    }, 200);
    return () => { alive = false; window.clearTimeout(t); };
  }, [adding, typeQ, category]);

  // The category chips, loaded once when the picker first opens.
  useEffect(() => {
    if (!adding || cats.length) return;
    let alive = true;
    fetch("/api/forge/node-categories")
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((d: { items: Category[] }) => { if (alive) setCats(d.items); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [adding, cats.length]);

  /** Records one change (state, title or delete) in the overlay, then reloads the graph. The imported file is not changed. */
  const change = async (body: { op: string; node: string; state?: string; title?: string }) => {
    setOpErr(null);
    const r = await fetch(`/api/forge/workflows/${workflowId}/graph/ops`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => null))?.detail;
      setOpErr(typeof detail === "string" ? detail : "The change could not be saved.");
      return;
    }
    setRenaming(false);
    setConfirmDelete(false);
    if (body.op === "delete") setSelected(null);
    setG(null);
  };

  const undoChange = async () => {
    setOpErr(null);
    const r = await fetch(`/api/forge/workflows/${workflowId}/graph/ops/undo`, { method: "POST" });
    if (!r.ok) {
      setOpErr("There is no change to undo.");
      return;
    }
    setG(null);
  };

  /** Copies an id or type for pasting elsewhere. Where the browser refuses clipboard access, nothing is copied and nothing breaks. */
  const copyText = async (key: string, text: string) => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(key);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      setCopied(null);
    }
  };

  /** Moves the view so the node sits in the middle of the canvas. */
  const centre = (id: string) => {
    const b = boxes.get(id);
    if (!b) return;
    const el = canvas.current;
    const cw = el?.clientWidth || 800;
    const ch = el?.clientHeight || 460;
    setView((v) => ({ ...v, x: cw / 2 - (b.x + b.w / 2) * v.zoom, y: ch / 2 - (b.y + b.h / 2) * v.zoom }));
  };

  /** Adds a node at the centre of what is on screen. It is kept beside the workflow; the saved file and runs are unchanged. */
  const addNode = async (type: string) => {
    const el = canvas.current;
    const cw = el?.clientWidth || 800;
    const ch = el?.clientHeight || 460;
    const pos = [(cw / 2 - view.x) / view.zoom, (ch / 2 - view.y) / view.zoom];
    setAddErr(null);
    const r = await fetch(`/api/forge/workflows/${workflowId}/graph/nodes`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type, pos }),
    });
    if (!r.ok) {
      // The detail is a message for a refusal, or a list of field errors for a bad request; only a message is shown.
      const detail = (await r.json().catch(() => null))?.detail;
      setAddErr(typeof detail === "string" ? detail : "The node could not be added. Check the node name and try again.");
      return;
    }
    setAdding(false);
    setTypeQ("");
    setG(null);   // the graph reloads with the new node
  };

  useEffect(() => {
    if (!open || g) return;
    fetch(`/api/forge/workflows/${workflowId}/graph`).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "The graph could not be read.");
      const d: Graph & { changes?: number } = await r.json();
      setG(d);
      setChanges(d.changes ?? 0);
    }).catch((e) => setErr(e instanceof Error ? e.message : "The graph could not be read."));
  }, [open, g, workflowId]);

  // The canvas shows the whole workflow, or only what is inside one opened subgraph box.
  const shown = useMemo(() => (g ? focusView(g, focus) : null), [g, focus]);

  // The app's controls and the node each one sets, for the lens. Read once, when the lens is first turned on.
  useEffect(() => {
    if (!lens || controls !== null) return;
    let alive = true;
    fetch(`/api/forge/workflows/${workflowId}/spec`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("The app's controls could not be read."))))
      .then((d: { spec?: { components?: { id: string; label: string; binding?: { node: string } }[] } }) => {
        if (alive) setControls((d.spec?.components ?? []).map((c) => ({ id: c.id, label: c.label, nodeId: c.binding?.node ?? "" })));
      })
      .catch(() => { if (alive) setControls([]); });
    return () => { alive = false; };
  }, [lens, controls, workflowId]);

  const exposed = useMemo(() => (controls ? new Set(controls.map((c) => c.nodeId)) : null), [controls]);

  // The trace: every node upstream of the selection (what feeds it) and downstream (what it feeds), over the whole workflow.
  const traced = useMemo(() => (g && selected && trace ? traceOf(g, selected) : null), [g, selected, trace]);
  const inTrace = (id: string) => !!traced && (id === selected || traced.up.has(id) || traced.down.has(id));
  const lay = useMemo(() => (shown ? layout(shown) : null), [shown]);
  const slots = useMemo(() => (shown ? slotCounts(shown) : { ins: new Map<string, number>(), outs: new Map<string, number>() }), [shown]);

  // Nodes in the current view whose title, type or id contain the find text, in the order they appear.
  const matches = useMemo(() => {
    const q = find.trim().toLowerCase();
    if (!q || !shown) return [];
    return shown.nodes.filter((n) => `${n.title} ${n.type} ${n.id}`.toLowerCase().includes(q));
  }, [find, shown]);
  const legend = useMemo(() => [...new Set((shown?.links ?? []).map((l) => l.type))].sort(), [shown]);

  const fit = (el: HTMLElement | null, b: Rect) => {
    const cw = el?.clientWidth || 800;
    const ch = el?.clientHeight || 460;
    const zoom = clamp(Math.min((cw - 48) / Math.max(b.w, 1), (ch - 48) / Math.max(b.h, 1), 1.2), 0.2, 1.2);
    setView({ zoom, x: (cw - b.w * zoom) / 2 - b.x * zoom, y: (ch - b.h * zoom) / 2 - b.y * zoom });
  };

  // A new graph starts fitted to the window, with no moved nodes.
  useEffect(() => {
    if (lay) { setMoved({}); fit(canvas.current, lay.bounds); }
  }, [lay]);

  // The wheel zooms around the cursor. It is attached directly so the page does not scroll.
  useEffect(() => {
    const el = canvas.current;
    if (!el || !g) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const cx = e.clientX - r.left;
      const cy = e.clientY - r.top;
      setView((v) => {
        const zoom = clamp(v.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), 0.2, 2.5);
        const k = zoom / v.zoom;
        return { zoom, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [g, open]);

  const boxes = useMemo(() => {
    const m = new Map<string, Box>();
    lay?.boxes.forEach((b, id) => {
      const p = moved[id];
      m.set(id, p ? { ...b, x: p[0], y: p[1] } : b);
    });
    return m;
  }, [lay, moved]);

  const down = (e: RPointerEvent<HTMLDivElement>, id?: string) => {
    if (e.button !== 0) return;
    const b = id ? boxes.get(id) : undefined;
    drag.current = id && b
      ? { kind: "node", id, sx: e.clientX, sy: e.clientY, ox: b.x, oy: b.y, moved: false }
      : { kind: "pan", sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y, moved: false };
    // Only the background captures the pointer. A node keeps its click, so selecting still works.
    if (drag.current.kind === "pan") canvas.current?.setPointerCapture(e.pointerId);
  };

  const move = (e: RPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (d.kind === "pan") setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }));
    else if (d.id) setMoved((m) => ({ ...m, [d.id!]: [d.ox + dx / view.zoom, d.oy + dy / view.zoom] }));
  };

  const up = (e: RPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "pan") canvas.current?.releasePointerCapture(e.pointerId);
    // A drag that moved is followed by a click on the same spot; this stops that click from selecting.
    justDragged.current = !!d?.moved;
  };

  const select = (id: string | null) => {
    if (justDragged.current) { justDragged.current = false; return; }
    setSelected(id);
  };

  const node = g?.nodes.find((n) => n.id === selected) ?? null;
  const title = (id: string) => g?.nodes.find((n) => n.id === id)?.title ?? id;
  const isSub = (n: GNode) => SUBGRAPH.test(n.type);

  return (
    <section className="graph-view" aria-labelledby="gv-h">
      <h3 id="gv-h" className="sub-h">Graph</h3>
      <button type="button" className="secondary small-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "Hide the graph" : "Show the graph"}
      </button>
      {open && err && <p className="warn-text" role="alert">{err}</p>}
      {open && !g && !err && <p className="small muted" role="status">Reading the graph…</p>}
      {open && g && lay && (
        <>
          <nav className="gv-crumbs" aria-label="Canvas location">
            <button type="button" className="gv-crumb" aria-current={focus === null ? "page" : undefined} onClick={() => { setFocus(null); setSelected(null); }}>Workflow</button>
            {trail(g.boxes ?? [], focus).map((b) => (
              <span key={b.scope} className="gv-crumb-part">
                <span aria-hidden="true" className="muted"> › </span>
                <button type="button" className="gv-crumb" aria-current={focus === b.scope ? "page" : undefined} onClick={() => { setFocus(b.scope); setSelected(null); }}>{b.name}</button>
              </span>
            ))}
          </nav>
          <p className="small muted">
            {shown?.nodes.length} nodes · {shown?.links.length} links{focus ? ` inside ${trail(g.boxes ?? [], focus).slice(-1)[0]?.name ?? "this box"}` : ""}. The imported file is read only. Changes made here are saved with the app, but runs do not use them yet.
          </p>
          {g.boxes && g.boxes.length > 0 && (
            <section className="gv-subgraphs" aria-label="Subgraph boxes in this workflow">
              <p className="small muted">Subgraphs open in place. Open a box to see only what is inside it.</p>
              <ul>
                {g.boxes.map((b) => {
                  const inside = g.nodes.filter((n) => n.scope === b.scope || (n.scope ?? "").startsWith(`${b.scope}:`)).length;
                  return (
                    <li key={b.scope} className="gv-box-row">
                      <span className="small"><strong>{b.label}</strong> <span className="muted">· {inside} nodes · box {b.scope}</span></span>
                      <button type="button" className="secondary small-btn" aria-current={focus === b.scope ? "true" : undefined} onClick={() => { setFocus(b.scope); setSelected(null); }}>
                        {focus === b.scope ? "Viewing" : "Open box"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          {g.note && <p className="small muted">{g.note}</p>}
          <div className="gv-toolbar">
            <button type="button" className="secondary small-btn" aria-label="Zoom in" onClick={() => setView((v) => ({ ...v, zoom: clamp(v.zoom * 1.2, 0.2, 2.5) }))}>+</button>
            <button type="button" className="secondary small-btn" aria-label="Zoom out" onClick={() => setView((v) => ({ ...v, zoom: clamp(v.zoom / 1.2, 0.2, 2.5) }))}>−</button>
            <button type="button" className="secondary small-btn" onClick={() => fit(canvas.current, lay.bounds)}>Fit</button>
            <span className="small muted">{Math.round(view.zoom * 100)}%</span>
            <button type="button" className="secondary small-btn" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
              {adding ? "Close" : "Add node"}
            </button>
            <input type="search" className="gv-find" aria-label="Find a node in this view" placeholder="Find a node (/)"
              value={find} ref={findRef} onChange={(e) => setFind(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) { setSelected(matches[0].id); centre(matches[0].id); } }} />
            {find.trim() && <span className="small muted" role="status">{matches.length} match{matches.length === 1 ? "" : "es"}</span>}
            <button type="button" className={`secondary small-btn${lens ? " on" : ""}`} aria-pressed={lens} onClick={() => setLens((v) => !v)}
              title="Highlights the nodes your app sets as controls">App lens</button>
            <button type="button" className={`secondary small-btn${trace ? " on" : ""}`} aria-pressed={trace} onClick={() => setTrace((v) => !v)}
              title="When a node is selected: what feeds it (green) and what it feeds (orange)">Trace flow</button>
          </div>
          {lens && (
            <div className="gv-lens" aria-label="App controls">
              {controls === null && <span className="small muted">Reading the app's controls…</span>}
              {controls?.length === 0 && <span className="small muted">This app has no saved controls yet. Save it in the builder to see them here.</span>}
              {controls && controls.length > 0 && (
                <>
                  <span className="small muted">{controls.length} control{controls.length > 1 ? "s" : ""}:</span>
                  {controls.map((c) => {
                    const onCanvas = g?.nodes.some((n) => n.id === c.nodeId);
                    return (
                      <button key={c.id} type="button" className="gv-ctl" disabled={!onCanvas}
                        title={onCanvas ? `Show the node this control sets (${c.nodeId})` : "This control's node is not on the canvas in this view"}
                        onClick={() => { setSelected(c.nodeId); centre(c.nodeId); }}>
                        {c.label}
                      </button>
                    );
                  })}
                </>
              )}
            </div>
          )}
          <div className="gv-legend" aria-label="Link colours">
            {legend.map((t) => (
              <span key={t} className="gv-key"><span className="gv-swatch" style={{ background: TYPE_COLOR[t] ?? LINK_DEFAULT }} />{t}</span>
            ))}
            <span className="small muted gv-keys">Keys: + − zoom · F fit · / find · Esc clear</span>
          </div>
          {adding && (
            <div className="gv-add" role="region" aria-label="Add a node">
              <input type="search" aria-label="Search ComfyUI nodes" placeholder="Search ComfyUI nodes, e.g. KSampler or upscale"
                value={typeQ} onChange={(e) => setTypeQ(e.target.value)} />
              <div className="gv-cats" role="group" aria-label="Node categories">
                <button type="button" className={`gv-cat${category === "" ? " on" : ""}`} aria-pressed={category === ""} onClick={() => setCategory("")}>All</button>
                {cats.map((c) => (
                  <button key={c.name} type="button" className={`gv-cat${category === c.name ? " on" : ""}`} aria-pressed={category === c.name}
                    onClick={() => setCategory(c.name)}>
                    {c.name} <span className="muted">{c.count}</span>
                  </button>
                ))}
              </div>
              {addErr && <p className="warn-text" role="alert">{addErr}</p>}
              <div className="gv-picker">
                <ul className="gv-hits" aria-label="ComfyUI nodes">
                  {hits.map((h) => (
                    <li key={h.type}>
                      <button type="button" className={`gv-hit${picked?.type === h.type ? " on" : ""}`} aria-pressed={picked?.type === h.type}
                        onClick={() => setPicked(h)}>
                        <span className="gv-hit-name">{h.type}</span>
                        <span className="small muted">{h.category}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {picked ? (
                  <div className="gv-spec" aria-live="polite">
                    <p><strong>{picked.type}</strong> <span className="small muted">{picked.category}</span></p>
                    <dl>
                      <dt className="small muted">Inputs</dt>
                      <dd className="small">{picked.inputs.length ? picked.inputs.map((i) => `${i.name} (${i.type})`).join(", ") : "none"}</dd>
                      <dt className="small muted">Settings</dt>
                      <dd className="small">{picked.widgets.length ? picked.widgets.join(", ") : "none"}</dd>
                      <dt className="small muted">Outputs</dt>
                      <dd className="small">{picked.outputs.length ? picked.outputs.join(", ") : "none"}{picked.output_node ? " · output node" : ""}</dd>
                    </dl>
                    <button type="button" className="gv-add-btn" onClick={() => void addNode(picked.type)}>Add to canvas</button>
                  </div>
                ) : (
                  <p className="small muted">Pick a node to see its inputs, settings and outputs.</p>
                )}
              </div>
              {hits.length === 0 && <p className="small muted">No node matches. Check the spelling or pick another category.</p>}
              <p className="small muted">The node is added to the canvas only. The saved workflow file and its runs do not change.</p>
            </div>
          )}
          <div
            ref={canvas}
            className="gv-canvas"
            role="img"
            aria-label={`Node graph with ${shown?.nodes.length ?? 0} nodes and ${shown?.links.length ?? 0} links`}
            onPointerDown={(e) => down(e)}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={up}
            onClick={() => select(null)}
          >
            {lay && shown && (
              <Minimap bounds={lay.bounds} boxes={boxes} view={view} canvas={canvas.current} selected={selected}
                onJump={(wx, wy) => setView((v) => ({ ...v, x: (canvas.current?.clientWidth ?? 800) / 2 - wx * v.zoom, y: (canvas.current?.clientHeight ?? 460) / 2 - wy * v.zoom }))} />
            )}
            <div className="gv-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
              <svg className="gv-links" width={1} height={1} overflow="visible">
                {shown?.links.map((l) => {
                  const a = boxes.get(l.from);
                  const b = boxes.get(l.to);
                  if (!a || !b) return null;
                  return (
                    <path key={l.id} d={curve(slotPoint(a, l.from_slot, "out"), slotPoint(b, l.to_slot, "in"))}
                      stroke={TYPE_COLOR[l.type] ?? LINK_DEFAULT}
                      className={`gv-link${selected && (l.from === selected || l.to === selected) ? " hl" : ""}${traced ? (inTrace(l.from) && inTrace(l.to) ? " trace" : " dim") : ""}`}>
                      <title>{l.type}: {title(l.from)} → {title(l.to)}</title>
                    </path>
                  );
                })}
              </svg>
              {shown?.nodes.map((n) => {
                const b = boxes.get(n.id);
                if (!b) return null;
                const cls = ["gv-node", selected === n.id && "sel", n.state !== "active" && "off", n.ui_only && "ui", isSub(n) && "sub",
                  traced?.up.has(n.id) && "up", traced?.down.has(n.id) && "down",
                  traced && !inTrace(n.id) && "dim",
                  lens && exposed && exposed.has(n.id) && "ctl", lens && exposed && !exposed.has(n.id) && "dim"]
                  .filter(Boolean).join(" ");
                const rows = rowCount(n, slots);
                return (
                  <div
                    key={n.id}
                    className={cls}
                    style={{ left: b.x, top: b.y, width: b.w, height: b.h, borderTopColor: n.state === "bypassed" ? "#ff5d5d" : undefined }}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected === n.id}
                    aria-label={`${n.title}, node ${n.id}, ${n.state}`}
                    onPointerDown={(e) => { e.stopPropagation(); down(e, n.id); }}
                    onClick={(e) => { e.stopPropagation(); select(n.id); }}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(n.id); } }}
                  >
                    <div className="gv-head">
                      <span className="gv-title">{n.title}</span>
                      {n.known === false && <span className="gv-unk" title="ComfyUI does not know this node type">unknown</span>}
                      <span className="gv-id">#{n.id}</span>
                    </div>
                    {n.group && <div className="gv-group" style={{ height: GROUP_LINE }}>in {n.group}</div>}
                    <div className="gv-body">
                      {Array.from({ length: rows }, (_, i) => {
                        const inp = n.inputs?.[i];
                        const out = n.outputs?.[i];
                        const inOn = i < (slots.ins.get(n.id) ?? 0);
                        const outOn = i < (slots.outs.get(n.id) ?? 0);
                        return (
                          <div key={i} className="gv-row">
                            <span className={`gv-dot in${inOn ? " on" : ""}`} style={inOn && inp ? { background: TYPE_COLOR[inp.type] ?? LINK_DEFAULT } : undefined} />
                            {inp && !inp.widget && <span className="gv-slot in">{inp.name}</span>}
                            {out && <span className="gv-slot out">{out.name}</span>}
                            <span className={`gv-dot out${outOn ? " on" : ""}`} style={outOn && out ? { background: TYPE_COLOR[out.type] ?? LINK_DEFAULT } : undefined} />
                          </div>
                        );
                      })}
                    </div>
                    {n.params.length > 0 && (
                      <div className="gv-params">
                        {n.params.slice(0, CARD_PARAMS).map((p, i) => (
                          <div key={`${p.name}-${i}`} className="gv-param" style={{ height: PARAM_LINE }}>
                            <span className="gv-pname">{p.name}</span>
                            <span className="gv-pvalue">{String(p.value)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          {node && (
            <div className="gv-selbar" role="toolbar" aria-label="Selected node">
              <span className="small"><strong>{node.title}</strong> <span className="muted">· id {node.id}</span></span>
              {node.scope && (
                <button type="button" className="secondary small-btn" onClick={() => { setFocus(node.scope ?? null); setSelected(node.id); }}>Open its box</button>
              )}
              <button type="button" className="secondary small-btn" onClick={() => void copyText("id", node.id)}>{copied === "id" ? "Copied" : "Copy id"}</button>
              <button type="button" className="secondary small-btn" onClick={() => void copyText("type", node.type)}>{copied === "type" ? "Copied" : "Copy type"}</button>
              <button type="button" className="secondary small-btn" onClick={() => centre(node.id)}>Centre</button>
              <button type="button" className="secondary small-btn" onClick={() => setSelected(null)}>Clear selection</button>
              <span className="gv-sep" aria-hidden="true" />
              {(["active", "muted", "bypassed"] as const).map((s) => (
                <button key={s} type="button" className={`secondary small-btn${node.state === s ? " on" : ""}`} aria-pressed={node.state === s}
                  onClick={() => void change({ op: "state", node: node.id, state: s })}>
                  {STATE_LABEL[s]}
                </button>
              ))}
              {renaming ? (
                <span className="gv-rename">
                  <input aria-label="New title" value={newTitle} maxLength={80} onChange={(e) => setNewTitle(e.target.value)} />
                  <button type="button" className="small-btn" disabled={!newTitle.trim()} onClick={() => void change({ op: "title", node: node.id, title: newTitle })}>Save title</button>
                  <button type="button" className="secondary small-btn" onClick={() => setRenaming(false)}>Cancel</button>
                </span>
              ) : (
                <button type="button" className="secondary small-btn" onClick={() => { setNewTitle(node.title); setRenaming(true); }}>Rename</button>
              )}
              {confirmDelete ? (
                <>
                  <button type="button" className="danger small-btn" onClick={() => void change({ op: "delete", node: node.id })}>Confirm remove</button>
                  <button type="button" className="secondary small-btn" onClick={() => setConfirmDelete(false)}>Keep it</button>
                </>
              ) : (
                <button type="button" className="secondary small-btn" onClick={() => setConfirmDelete(true)}>Remove from canvas</button>
              )}
              {opErr && <span className="warn-text small" role="alert">{opErr}</span>}
            </div>
          )}
          {changes > 0 && (
            <p className="small muted gv-undo">
              {changes} change{changes > 1 ? "s" : ""} saved on top of the workflow file.{" "}
              <button type="button" className="secondary small-btn" onClick={() => void undoChange()}>Undo last change</button>
            </p>
          )}
          {node ? (
            <div className="gv-detail" aria-live="polite">
              <p><strong>{node.title}</strong> <span className="small muted">#{node.id} · {node.type}</span>
                {node.state !== "active" && <span className="pill muted">{node.state}</span>}
                {node.ui_only && <span className="pill muted">editor only</span>}
                {isSub(node) && <span className="pill muted">subgraph</span>}
                {node.added && <span className="pill muted">added on the canvas, not wired into runs</span>}</p>
              {node.known === false && <p className="small warn-text">ComfyUI does not know this node type, so its settings show the names saved in the file.</p>}
              {node.params.length > 0 && (
                <div className="gv-settings">
                  <p className="small muted">Settings ({node.params.length})</p>
                  <table className="gv-table">
                    <thead>
                      <tr><th>Name</th><th>Type</th><th>Value</th><th>Limits</th></tr>
                    </thead>
                    <tbody>
                      {node.params.map((p, i) => (
                        <tr key={`${p.name}-${i}`}>
                          <td><code>{p.name}</code></td>
                          <td className="small muted">{p.type ?? "saved"}</td>
                          <td className="small">{String(p.value)}</td>
                          <td className="small muted">{limitsText(p)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="gv-io">
                <div>
                  <p className="small muted">Inputs</p>
                  <ul>
                    {g.links.filter((l) => l.to === node.id).map((l) => (
                      <li key={l.id} className="small">input {l.to_slot} ← {title(l.from)} <span className="muted">(output {l.from_slot}, {l.type})</span></li>
                    ))}
                    {!g.links.some((l) => l.to === node.id) && <li className="small muted">none connected</li>}
                  </ul>
                </div>
                <div>
                  <p className="small muted">Outputs</p>
                  <ul>
                    {g.links.filter((l) => l.from === node.id).map((l) => (
                      <li key={l.id} className="small">output {l.from_slot} → {title(l.to)} <span className="muted">(input {l.to_slot}, {l.type})</span></li>
                    ))}
                    {!g.links.some((l) => l.from === node.id) && <li className="small muted">not connected</li>}
                  </ul>
                </div>
              </div>
            </div>
          ) : (
            <p className="small muted">Select a node to see what it connects to.</p>
          )}
        </>
      )}
    </section>
  );
}
