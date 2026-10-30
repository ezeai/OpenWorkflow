import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, addEdge, applyEdgeChanges, applyNodeChanges,
  type Connection, type Edge, type Node, type NodeProps, type NodeChange, type EdgeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ApiError, api_canvas, type DirectorView, directorApi } from "./api";

type ServerNode = { id: string; type: string; x: number; y: number; params: Record<string, string> };
type ServerEdge = { id: string; src: string; src_port: string; dst: string; dst_port: string };
type Validation = { ok: boolean; errors: { node?: string; edge?: string; code: string; message: string }[]; warnings: { node?: string; message: string }[]; run_plan: { node: string; shot_id: string }[] };
type GraphFull = { id: string; name: string; nodes: ServerNode[]; edges: ServerEdge[]; validation: Validation; updated_at: string };

const PORT_COLOR: Record<string, string> = { shot_spec: "#e8b04a", character: "#7fb6ff", location: "#7fd6a4", asset: "#d89cff", report: "#9aa1ad" };
const NODE_LABEL: Record<string, string> = { shot: "Shot", character: "Character", location: "Location", zimage: "Generate: Z-Image Turbo", report: "Report" };
const INPUTS: Record<string, [string, string][]> = { shot: [], character: [], location: [], zimage: [["shot", "shot_spec"]], report: [["asset", "asset"]] };
const OUTPUTS: Record<string, [string, string][]> = { shot: [["shot", "shot_spec"]], character: [["character", "character"]], location: [["location", "location"]], zimage: [["asset", "asset"]], report: [] };

/** Start-from-template cards for the canvas home (pattern from Higgsfield Canvas templates). */
const TEMPLATES: { id: string; name: string; desc: string; preview: ReactNode }[] = [
  {
    id: "shot_to_image",
    name: "Shot to image",
    desc: "The minimal pipeline: one Director shot feeds one generator. Pick a shot, run it, get a take.",
    preview: (
      <svg className="canvas-tpl-svg" viewBox="0 0 190 62" fill="none" aria-hidden="true">
        <rect x="4" y="22" width="56" height="18" rx="6" fill="#d38131" fillOpacity=".16" stroke="#e8b04a" strokeOpacity=".55" />
        <circle cx="14" cy="31" r="4" fill="#e8b04a" />
        <text x="24" y="35" fill="#f2f2f3" fontSize="9">Shot</text>
        <path d="M60 31 h26" stroke="#2a2a31" strokeWidth="1.75" strokeLinecap="round" />
        <rect x="130" y="10" width="56" height="20" rx="6" fill="#d1fe17" fillOpacity=".14" stroke="#d1fe17" strokeOpacity=".55" />
        <circle cx="140" cy="20" r="4" fill="#d1fe17" />
        <text x="150" y="24" fill="#f2f2f3" fontSize="9">Image</text>
      </svg>
    ),
  },
  {
    id: "character_location_to_image",
    name: "Character + Location",
    desc: "A character and a location flow into the generator, so the take carries both with it — the two inputs Open Workflow checks by type.",
    preview: (
      <svg className="canvas-tpl-svg" viewBox="0 0 190 62" fill="none" aria-hidden="true">
        <rect x="2" y="8" width="58" height="18" rx="6" fill="#4d6ea3" fillOpacity=".18" stroke="#7ab0ff" strokeOpacity=".55" />
        <circle cx="12" cy="17" r="4" fill="#7ab0ff" />
        <text x="22" y="21" fill="#f2f2f3" fontSize="9">Char</text>
        <rect x="2" y="36" width="58" height="18" rx="6" fill="#3f6b54" fillOpacity=".18" stroke="#5bd49a" strokeOpacity=".55" />
        <circle cx="12" cy="45" r="4" fill="#5bd49a" />
        <text x="22" y="49" fill="#f2f2f3" fontSize="9">World</text>
        <path d="M60 17 c30 0 40 16 70 15" stroke="#2a2a31" strokeWidth="1.75" strokeLinecap="round" />
        <path d="M60 45 c30 0 40 -14 70 -13" stroke="#2a2a31" strokeWidth="1.75" strokeLinecap="round" />
        <rect x="130" y="22" width="56" height="20" rx="6" fill="#d1fe17" fillOpacity=".14" stroke="#d1fe17" strokeOpacity=".55" />
        <circle cx="140" cy="32" r="4" fill="#d1fe17" />
        <text x="150" y="36" fill="#f2f2f3" fontSize="9">Image</text>
      </svg>
    ),
  },
];

function OFNode({ data, id }: NodeProps) {
  const d = data as { type: string; params: Record<string, string>; setParam: (nid: string, k: string, v: string) => void; options: DirectorView | null };
  const opts = d.options;
  return (
    <div className="of-node">
      <div className="of-node-head">
        <span className={`of-node-dot dot-${d.type}`} aria-hidden="true" />
        <span className="of-node-title">{NODE_LABEL[d.type] ?? d.type}</span>
      </div>
      <div className="of-node-body">
        {d.type === "shot" && (
          <select aria-label="Shot" value={d.params.shot_id ?? ""} onChange={(e) => d.setParam(id, "shot_id", e.target.value)} className="nodrag">
            <option value="">Choose a shot…</option>
            {opts?.shots.map((s) => <option key={s.id} value={s.id}>Shot {s.number}: {s.shot_size.replace(/_/g, " ")}</option>)}
          </select>
        )}
        {d.type === "character" && (
          <select aria-label="Character" value={d.params.entity_id ?? ""} onChange={(e) => d.setParam(id, "entity_id", e.target.value)} className="nodrag">
            <option value="">Choose a character…</option>
            {opts?.characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
        {d.type === "location" && (
          <select aria-label="Location" value={d.params.entity_id ?? ""} onChange={(e) => d.setParam(id, "entity_id", e.target.value)} className="nodrag">
            <option value="">Choose a location…</option>
            {opts?.locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        )}
        {d.type === "zimage" && <div className="small muted">Runs the same job as “Generate take”.</div>}
        {INPUTS[d.type]?.map(([name, t]) => (
          <div key={`in-${name}`} className="of-port in">
            <Handle type="target" position={Position.Left} id={name} style={{ background: PORT_COLOR[t] }} />
            <span>{name} <span className="muted">({t.replace("_", " ")})</span></span>
          </div>
        ))}
        {OUTPUTS[d.type]?.map(([name, t]) => (
          <div key={`out-${name}`} className="of-port out">
            <span>{name} <span className="muted">({t.replace("_", " ")})</span></span>
            <Handle type="source" position={Position.Right} id={name} style={{ background: PORT_COLOR[t] }} />
          </div>
        ))}
      </div>
    </div>
  );
}

const nodeTypes = { openworkflow: OFNode };

function portType(nodeType: string, port: string, side: "in" | "out"): string | null {
  const list = side === "in" ? INPUTS[nodeType] : OUTPUTS[nodeType];
  return list?.find(([n]) => n === port)?.[1] ?? null;
}

export function Canvas({ projectId }: { projectId: string }) {
  const [graphs, setGraphs] = useState<{ id: string; name: string; valid: boolean; nodes: number }[] | null>(null);
  const [gid, setGid] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphFull | null>(null);
  const [director, setDirector] = useState<DirectorView | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [rev, setRev] = useState(0);
  const [status, setStatus] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const saving = useRef(false);
  const pending = useRef(false);

  const loadList = useCallback(async () => {
    try {
      const r = await api_canvas.list(projectId);
      setGraphs(r.graphs);
      setRev(r.revision);
      setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : "Could not load graphs."); }
  }, [projectId]);

  useEffect(() => { void loadList(); directorApi.get(projectId).then(setDirector).catch(() => undefined); }, [loadList, projectId]);

  const openGraph = useCallback(async (id: string) => {
    const g = await api_canvas.get(projectId, id);
    setGraph(g);
    setRev(g.revision);
    setGid(id);
    setValidation(g.validation);
    setNodes(g.nodes.map((n) => ({ id: n.id, type: "openworkflow", position: { x: n.x, y: n.y }, data: { type: n.type, params: n.params, setParam, options: director } })));
    setEdges(g.edges.map((e) => ({ id: e.id, source: e.src, sourceHandle: e.src_port, target: e.dst, targetHandle: e.dst_port })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, director]);

  // keep node option lists fresh without losing positions
  useEffect(() => {
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, options: director } })));
  }, [director]);

  const persist = useCallback(async (nextNodes: Node[], nextEdges: Edge[]) => {
    if (!gid) return;
    if (saving.current) { pending.current = true; return; }
    saving.current = true;
    setStatus("Saving…");
    try {
      const body = {
        base_revision: rev,
        nodes: nextNodes.map((n) => ({ id: n.id, type: (n.data as { type: string }).type, x: n.position.x, y: n.position.y,
                                       params: (n.data as { params: Record<string, string> }).params })),
        edges: nextEdges.map((e) => ({ id: e.id, src: e.source, src_port: e.sourceHandle ?? "", dst: e.target, dst_port: e.targetHandle ?? "" })),
      };
      const r = await api_canvas.save(projectId, gid, body);
      setRev(r.revision);
      setValidation(r.validation);
      setStatus("Saved");
      setErr(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { setErr("Changed elsewhere. Reload the graph."); }
      else setErr(e instanceof Error ? e.message : "Save failed.");
      setStatus("Save failed");
    } finally {
      saving.current = false;
      if (pending.current) { pending.current = false; void persist(nextNodes, nextEdges); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gid, projectId, rev]);

  const scheduleSave = useCallback((n: Node[], e: Edge[]) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void persist(n, e), 500);
  }, [persist]);

  function setParam(nid: string, k: string, v: string) {
    setNodes((ns) => {
      const next = ns.map((n) => (n.id === nid ? { ...n, data: { ...n.data, params: { ...(n.data as { params: object }).params, [k]: v } } } : n));
      scheduleSave(next, edges);
      return next;
    });
  }

  const onNodesChange = (changes: NodeChange[]) => {
    setNodes((ns) => {
      const next = applyNodeChanges(changes, ns);
      if (changes.some((c) => c.type === "remove" || (c.type === "position" && c.dragging === false))) scheduleSave(next, edges);
      return next;
    });
  };
  const onEdgesChange = (changes: EdgeChange[]) => {
    setEdges((es) => {
      const next = applyEdgeChanges(changes, es);
      if (changes.some((c) => c.type === "remove")) scheduleSave(nodes, next);
      return next;
    });
  };

  const isValidConnection = (c: Connection | Edge) => {
    const s = nodes.find((n) => n.id === c.source);
    const t = nodes.find((n) => n.id === c.target);
    if (!s || !t) return false;
    const st = portType((s.data as { type: string }).type, c.sourceHandle ?? "", "out");
    const tt = portType((t.data as { type: string }).type, c.targetHandle ?? "", "in");
    return !!st && st === tt && c.source !== c.target;
  };

  const onConnect = (c: Connection) => {
    if (!isValidConnection(c)) return;
    setEdges((es) => {
      const withoutOld = es.filter((e) => !(e.target === c.target && e.targetHandle === c.targetHandle));
      const next = addEdge({ ...c, id: crypto.randomUUID().replace(/-/g, "") }, withoutOld);
      scheduleSave(nodes, next);
      return next;
    });
  };

  const addNode = (type: string) => {
    const id = crypto.randomUUID().replace(/-/g, "");
    const n: Node = { id, type: "openworkflow", position: { x: 60 + nodes.length * 30, y: 60 + nodes.length * 20 },
      data: { type, params: {}, setParam, options: director } };
    const next = [...nodes, n];
    setNodes(next);
    scheduleSave(next, edges);
  };

  const run = async () => {
    if (!gid) return;
    setRunMsg(null);
    setErr(null);
    try {
      const r = await api_canvas.run(projectId, gid, rev);
      setRunMsg(`${r.jobs.length} job(s) started. Track them on the Jobs page.`);
      void directorApi.get(projectId).then(setDirector);
    } catch (e) {
      if (e instanceof ApiError) setErr(e.message);
      else setErr(e instanceof Error ? e.message : "Run failed.");
    }
  };

  if (!graphs) return <div className="page"><div className="skeleton" aria-busy="true" style={{ height: 160 }} /></div>;

  if (!gid) {
    return (
      <div className="page wide">
        <header className="page-head"><div><h1>Canvas</h1><p className="lede">Connect shots, characters and locations to generators. Connections are checked by type before anything runs.</p></div></header>
        {err && <div className="banner bad" role="alert">{err}</div>}
        <h2 style={{ marginBottom: 10 }}>Start from a template</h2>
        <div className="canvas-tpl-grid">
          {TEMPLATES.map((tpl) => (
            <button key={tpl.id} className="canvas-tpl" onClick={async () => {
              try {
                const g = await api_canvas.create(projectId, rev, tpl.name, tpl.id === "shot_to_image" ? "shot_to_image" : "character_location_to_image");
                await loadList();
                await openGraph(g.id);
              } catch (e) { setErr(e instanceof Error ? e.message : "Could not create graph."); }
            }}>
              <figure>{tpl.preview}</figure>
              <div>
                <div className="canvas-tpl-name">{tpl.name}</div>
                <p className="small muted">{tpl.desc}</p>
              </div>
              <span className="canvas-tpl-cta">Start from this template →</span>
            </button>
          ))}
        </div>
        <h2 style={{ margin: "22px 0 10px" }}>Your graphs</h2>
        <ul className="project-list">
          {graphs.map((g) => (
            <li key={g.id} className="card project-row">
              <div><strong>{g.name}</strong><div className="small muted">{g.nodes} node(s) · {g.valid ? "valid" : "needs fixing"}</div></div>
              <button className="secondary" onClick={() => void openGraph(g.id)}>Open</button>
            </li>
          ))}
        </ul>
        {graphs.length === 0 && <div className="empty-note">No graphs yet. Start from a template above — a template only lays out the nodes; you attach your own shots and entities.</div>}
      </div>
    );
  }

  return (
    <div className="page wide canvas-page">
      <div className="page-head">
        <div>
          <button className="ghost" onClick={() => { setGid(null); setGraph(null); void loadList(); }}>← All graphs</button>
          <h1 style={{ marginTop: 6 }}>{graph?.name}</h1>
          <p className="lede">The pipeline behind a take, as typed nodes. Ports only connect to ports of the same type.</p>
          <div className="port-legend" aria-label="Port types">
            <span className="small muted">Port types:</span>
            {["TEXT", "IMAGE", "MODEL", "LATENT"].map((t) => <span key={t} className={`port-chip port-${t.toLowerCase()}`}>{t}</span>)}
          </div>
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <span className="save-status" role="status">{status}</span>
          <button className="secondary" onClick={() => void persist(nodes, edges)}>Validate</button>
          <button onClick={run} disabled={!validation?.ok}>Run</button>
        </div>
      </div>
      {err && <div className="banner bad" role="alert">{err}</div>}
      {runMsg && <div className="banner info" role="status">{runMsg}</div>}

      <div className="canvas-grid">
        <aside className="palette" aria-label="Add node">
          <h2>Add node</h2>
          {Object.entries(NODE_LABEL).map(([t, label]) => (
            <button key={t} className="ghost palette-btn" onClick={() => addNode(t)}>+ {label}</button>
          ))}
          <section className="validation" aria-label="Validation">
            <h2>Check</h2>
            {validation?.ok ? <p className="small ok">Graph is valid. {validation.run_plan.length} generator(s) would run.</p> : <p className="small warn-text">Not runnable yet.</p>}
            <ul className="small">
              {validation?.errors.map((e, i) => <li key={i} className="w-error">{e.message}</li>)}
              {validation?.warnings.map((w, i) => <li key={`w${i}`} className="warn-text">{w.message}</li>)}
            </ul>
          </section>
        </aside>
        <div className="canvas-area canvas-board">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color="#2e2e38" />
            <Controls />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}

