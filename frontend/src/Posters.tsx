import { useCallback, useEffect, useState } from "react";
import { mossApi, type MossStatus, type Poster, type PosterBox, type PromptRequest } from "./api";
import { LayoutEditor, type BoxTool } from "./LayoutEditor";

const MAX_PROMPT = 1500;
const DEFAULT_TEXT_STYLE = "bold headline, clean sans-serif, high contrast";

type Summary = { id: string; state: string; created_at: string; headline: string; prompt: string };
type Preset = { id: string; name: string; description: string; boxes: PosterBox[] };
type Aspect = { id: string; name: string; use: string; sizes: Record<string, [number, number]> };


/** Product and poster design with MOSS-Image. The layout is drawn as boxes; the exact JSON MOSS reads is shown too. */
export function Posters() {
  const [status, setStatus] = useState<MossStatus | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [prompt, setPrompt] = useState("");
  const [boxes, setBoxes] = useState<PosterBox[]>([]);
  const [aspect, setAspect] = useState("square");
  const [base, setBase] = useState(512);
  const [aspects, setAspects] = useState<Aspect[]>([]);
  const [seed, setSeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [current, setCurrent] = useState<Poster | null>(null);
  const [recent, setRecent] = useState<Summary[]>([]);
  const [layoutErr, setLayoutErr] = useState<string | null>(null);
  const [layoutJson, setLayoutJson] = useState<unknown>(null);
  const [helper, setHelper] = useState<PromptRequest | null>(null);
  const [helperSince, setHelperSince] = useState<number | null>(null);
  const [engine, setEngine] = useState<"fast" | "encoder">("fast");
  const [tool, setTool] = useState<BoxTool>("obj");
  const [warnings, setWarnings] = useState<string[][]>([]);

  const refreshRecent = useCallback(() => {
    mossApi.list().then((r) => setRecent(r.posters)).catch(() => setRecent([]));
  }, []);

  useEffect(() => {
    mossApi.status().then(setStatus).catch(() => setStatus({ state: "not_running", url: "", message: "Could not check the MOSS server." }));
    mossApi.aspects().then((r) => setAspects(r.aspects)).catch(() => undefined);
    mossApi.presets().then((r) => { setPresets(r.presets); setBoxes(r.presets[0]?.boxes ?? []); }).catch(() => undefined);
    refreshRecent();
  }, [refreshRecent]);

  // A poster that is rendering is checked until it completes or fails.
  useEffect(() => {
    if (!current || current.state !== "running") return;
    const t = window.setInterval(() => {
      mossApi.get(current.id).then((r) => {
        setCurrent(r.poster);
        if (r.poster.state !== "running") refreshRecent();
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(t);
  }, [current, refreshRecent]);

  // The prompt helper: checked every 2 seconds until the text encoder has written the prompt.
  useEffect(() => {
    if (!helper || helper.state !== "running") return;
    const t = window.setInterval(() => {
      mossApi.suggestion(helper.id).then((r) => {
        setHelper(r.request);
        if (r.request.state === "done" && r.request.prompt) setPrompt(r.request.prompt);
      }).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(t);
  }, [helper]);

  // The layout is checked as the boxes change. The JSON below is exactly what MOSS receives.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (boxes.some((b) => b.type === "text" && !(b.text ?? "").trim())) {
        setLayoutErr("Type the words for each text box, or remove the box.");
        setLayoutJson(null);
        setWarnings([]);
        return;
      }
      mossApi.layout({ boxes, aspect, base, prompt: prompt || "poster" })
        .then((r) => { setLayoutErr(null); setLayoutJson(r.layout); setWarnings(r.warnings ?? []); })
        .catch((e) => { setLayoutErr(e instanceof Error ? e.message : "The layout could not be checked."); setLayoutJson(null); setWarnings([]); });
    }, 350);
    return () => window.clearTimeout(t);
  }, [boxes, aspect, base, prompt]);

  const textBox = boxes.find((b) => b.type === "text");
  const headline = textBox?.text ?? "";
  const rendering = current?.state === "running";
  const shape = aspects.find((a) => a.id === aspect)?.sizes[String(base)];
  const ratio = shape && shape[1] ? shape[0] / shape[1] : 1;
  const canRun = status?.state === "ready" && prompt.trim().length > 0 && boxes.length > 0 && !layoutErr && !busy && !rendering;

  const applyPreset = (p: Preset) => {
    const keep = headline;
    setBoxes(p.boxes.map((b) => (b.type === "text" ? { ...b, text: keep } : b)));
  };
  const setHeadline = (value: string) => {
    setBoxes((cur) => {
      const idx = cur.findIndex((b) => b.type === "text");
      if (idx === -1) return value ? [...cur, { type: "text", x: 0.08, y: 0.8, w: 0.84, h: 0.13, desc: DEFAULT_TEXT_STYLE, text: value }] : cur;
      return cur.map((b, j) => (j === idx ? { ...b, text: value } : b));
    });
  };

  const suggest = async () => {
    setErr(null);
    try {
      const r = await mossApi.suggest(prompt.trim() || "a product in a clean advertising scene", engine);
      setHelperSince(Date.now());
      setHelper(r.request);
      if (r.request.state === "done" && r.request.prompt) setPrompt(r.request.prompt);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The prompt helper could not start.");
    }
  };

  const generate = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await mossApi.create({ prompt: prompt.trim(), headline: "", aspect, base, seed, boxes });
      setCurrent(r.poster);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "The poster could not start.");
    } finally {
      setBusy(false);
    }
  };

  const open = async (id: string) => {
    setErr(null);
    try { setCurrent((await mossApi.get(id)).poster); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not open that poster."); }
  };

  return (
    <div className="page wide posters">
      <header className="page-head">
        <div>
          <p className="eyebrow">Forge</p>
          <h1>Posters</h1>
          <p className="lede">Describe the scene, place the product and the headline on the canvas, then generate. MOSS-Image draws the headline exactly as written. Nothing leaves this computer.</p>
        </div>
      </header>

      {status && status.state !== "ready" && (
        <div className="banner bad" role="alert">{status.message} <span className="small muted">{status.url}</span></div>
      )}
      {err && <div className="banner bad" role="alert">{err}</div>}

      <div className="posters-grid">
        <section className="poster-form" aria-labelledby="pf-h">
          <h2 id="pf-h" className="sub-h">Scene</h2>
          <label className="r-field">
            <span>Describe the product and the scene</span>
            <textarea rows={4} maxLength={MAX_PROMPT} value={prompt} onChange={(e) => setPrompt(e.target.value)}
              placeholder="A glass perfume bottle on black stone, dramatic rim light, luxury advertisement" />
            <span className="small muted">{prompt.length} / {MAX_PROMPT}</span>
          </label>
          <div className="helper-row">
            <label className="small">Helper
              <select value={engine} onChange={(e) => setEngine(e.target.value as "fast" | "encoder")}>
                <option value="fast">Fast: local chat model (seconds)</option>
                <option value="encoder">MOSS text encoder (several minutes)</option>
              </select>
            </label>
            <button type="button" className="secondary" onClick={() => void suggest()} disabled={helper?.state === "running" || status?.state !== "ready"}>
              {helper?.state === "running" ? `Writing with the text encoder… ${helperSince ? Math.floor((Date.now() - helperSince) / 1000) : 0} s` : "Suggest a prompt"}
            </button>
            {helper?.state === "done" && helper.source === "moss_text_encoder" && <span className="pill good">Written by the MOSS text encoder</span>}
            {helper?.state === "done" && helper.source === "local_model" && <span className="pill good">Written by a local chat model</span>}
            {helper?.state === "done" && helper.source === "template" && <span className="pill muted">Template</span>}
          </div>
          {helper?.state === "running" && <p className="small muted">The MOSS text encoder takes several minutes on this computer. You can keep editing the layout while it writes.</p>}
          {helper?.note && <p className="small warn-text" role="status">{helper.note}</p>}

          <h2 className="sub-h">Layout</h2>
          <div className="preset-row" role="group" aria-label="Layout presets">
            {presets.map((p) => (
              <button key={p.id} type="button" className="lib-chip" onClick={() => applyPreset(p)} title={p.description}>{p.name}</button>
            ))}
          </div>
          <div className="layout-editor">
            <LayoutEditor boxes={boxes} onChange={setBoxes} warnings={warnings} tool={tool} onTool={setTool} ratio={ratio} />
            {layoutErr && <p className="box-hint" role="status">{layoutErr}</p>}
            <label className="r-field"><span>Headline (shortcut for the first text box)</span>
              <input type="text" maxLength={200} value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="NEW SCENT" /></label>
          </div>

          <h2 className="sub-h">Poster type</h2>
          <div className="aspect-grid" role="radiogroup" aria-label="Poster type">
            {aspects.map((a) => (
              <button key={a.id} type="button" role="radio" aria-checked={aspect === a.id} className={`aspect-card${aspect === a.id ? " on" : ""}`} onClick={() => setAspect(a.id)}>
                <strong>{a.name}</strong>
                <span className="small muted">{a.use}</span>
                <span className="small">{(a.sizes[String(base)] ?? [0, 0]).join(" × ")} px</span>
              </button>
            ))}
          </div>
          <div className="posters-row">
            <label className="r-field"><span>Quality</span>
              <select value={base} onChange={(e) => setBase(Number(e.target.value))}>
                <option value={512}>Draft (short side 512)</option>
                <option value={1024}>Final (short side 1024)</option>
              </select></label>
            <label className="r-field"><span>Seed</span>
              <input type="number" min={0} value={seed} onChange={(e) => setSeed(Math.max(0, Math.trunc(Number(e.target.value) || 0)))} /></label>
          </div>

          <details className="layout-json">
            <summary>What MOSS receives</summary>
            {!layoutErr && layoutJson ? <pre>{JSON.stringify(layoutJson, null, 2)}</pre> : !layoutErr && <p className="muted small">Add a box to see the layout.</p>}
          </details>

          <button type="button" className="r-generate" onClick={() => void generate()} disabled={!canRun}>
            {busy ? "Starting…" : rendering ? "Rendering…" : "Generate poster"}
          </button>
          <p className="small muted">Runs the MOSS model on this computer. The first render after a restart is slower while the model loads.</p>
        </section>

        <section className="poster-result" aria-live="polite" aria-labelledby="pr-h">
          <h2 id="pr-h" className="sub-h">Result</h2>
          {!current && <p className="muted small">Your poster appears here. Each finished poster keeps a receipt with the settings and checksums.</p>}
          {current?.state === "running" && <div className="indeterminate" aria-label="Rendering" role="status" />}
          {current?.state === "failed" && <p className="warn-text">{current.message ?? "This poster did not finish."}</p>}
          {current?.state === "completed" && (
            <>
              <img className="poster-img" src={mossApi.outputUrl(current.id)} alt={current.params.headline ? `Poster with the headline ${current.params.headline}` : "Generated poster"} />
              <div className="run-actions">
                <a className="small" href={mossApi.outputUrl(current.id)} download={`poster-${current.id.slice(0, 8)}.png`}>Download the image</a>
                <a className="small" href={mossApi.receiptUrl(current.id)} download={`receipt-${current.id.slice(0, 8)}.json`}>Download the receipt</a>
              </div>
            </>
          )}
        </section>
      </div>

      <section className="poster-recent" aria-labelledby="pp-h">
        <h2 id="pp-h" className="sub-h">Recent posters</h2>
        {recent.length === 0 && <p className="muted small">No posters yet.</p>}
        <ul className="run-list">
          {recent.map((p) => (
            <li key={p.id} className="run-item">
              <div className="run-meta">
                <span className={`pill ${p.state === "completed" ? "good" : p.state === "failed" ? "bad" : "muted"}`}>{p.state}</span>
                <span className="small muted">{new Date(p.created_at).toLocaleString()}</span>
                <span className="small">{p.headline || p.prompt.slice(0, 60)}</span>
                <span className="run-actions"><button type="button" className="ghost small-btn" onClick={() => void open(p.id)}>Open</button></span>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
