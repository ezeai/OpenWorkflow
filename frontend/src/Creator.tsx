import { useEffect, useState } from "react";
import { forgeCreatorApi, forgeOperatorApi, jobsApi, type Character, type ForgeWorkflow, type OperatorEndpoint } from "./api";

// Creator path: an idea (and optionally a reference photo) becomes a portrait prompt written by a local model.
// The prompt is shown for editing, then rendered by the Krea-2 creator app. A finished render can become the anchor.

const JOB_DONE = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"]);
const CREATOR_NAME = /creator|krea/i;

export function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
    r.onerror = () => reject(new Error("the photo could not be read"));
    r.readAsDataURL(file);
  });
}

export function Creator({ apps, character, onAnchor }: { apps: ForgeWorkflow[]; character: Character | null; onAnchor: (c: Character) => void }) {
  const [endpoints, setEndpoints] = useState<OperatorEndpoint[]>([]);
  const [endpointId, setEndpointId] = useState("");
  const [model, setModel] = useState("");
  const [idea, setIdea] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [prompt, setPrompt] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [renders, setRenders] = useState<{ id: string; seed: number; state: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    forgeOperatorApi.endpoints().then(({ endpoints: list }) => {
      setEndpoints(list);
      const ready = list.find((e) => e.state === "ok");
      setEndpointId(ready?.id ?? "");
      setModel(ready?.models[0] ?? "");
    }).catch(() => setEndpoints([]));
  }, []);

  useEffect(() => {
    const creators = apps.filter((w) => CREATOR_NAME.test(w.name) && w.readiness !== "blocked");
    setWorkflowId((cur) => cur || creators[0]?.id || "");
  }, [apps]);

  const current = endpoints.find((e) => e.id === endpointId) ?? null;

  const writePrompt = async () => {
    setError(null); setBusy(true);
    try {
      const reference_b64 = photo ? await readAsBase64(photo) : null;
      const r = await forgeCreatorApi.prompt({ idea, reference_b64, endpoint: endpointId, model });
      setPrompt(r.prompt);
    } catch (e) { setError(e instanceof Error ? e.message : "The prompt could not be written."); }
    finally { setBusy(false); }
  };

  const render = async () => {
    setError(null);
    try {
      const r = await forgeCreatorApi.runs({ workflow_id: workflowId, prompt, count: 2, seed_base: Math.floor(Math.random() * 900000) + 100 });
      setRenders(r.jobs.map((j) => ({ id: j.id, seed: j.seed, state: j.state })));
      for (const j of r.jobs) {
        for (let i = 0; i < 900; i++) {
          const s = await jobsApi.get(j.id);
          setRenders((rs) => rs.map((x) => (x.id === j.id ? { ...x, state: s.state } : x)));
          if (JOB_DONE.has(s.state)) break;
          await new Promise((res) => setTimeout(res, 3000));
        }
      }
    } catch (e) { setError(e instanceof Error ? e.message : "The renders could not start."); }
  };

  const useAsAnchor = async (jobId: string) => {
    if (!character) return;
    setError(null);
    try { onAnchor((await forgeCreatorApi.anchorFromRun(character.id, jobId)).character); }
    catch (e) { setError(e instanceof Error ? e.message : "That render could not become the anchor."); }
  };

  const creators = apps.filter((w) => CREATOR_NAME.test(w.name) && w.readiness !== "blocked");

  return (
    <section className="card creator" aria-labelledby="creator-h">
      <h2 id="creator-h">Create from an idea</h2>
      <p className="muted">A local model writes the portrait prompt. A reference photo gives it the look (hair, skin, build, clothing), not the exact face: Krea-2 has no identity input.</p>
      {error && <div className="banner bad" role="alert">{error}</div>}

      <label>Idea
        <textarea rows={2} maxLength={600} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="A calm nurse in her thirties, warm and confident" />
      </label>
      <label>Reference photo (optional)
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
      </label>
      <div className="creator-setup">
        <label>Model server
          <select value={endpointId} onChange={(e) => { setEndpointId(e.target.value); setModel(endpoints.find((x) => x.id === e.target.value)?.models[0] ?? ""); }}>
            <option value="">Choose a server</option>
            {endpoints.map((e) => <option key={e.id} value={e.id} disabled={e.state !== "ok"}>{e.label}{e.state === "ok" ? "" : " (not running)"}</option>)}
          </select>
        </label>
        <label>Model
          <select value={model} onChange={(e) => setModel(e.target.value)} disabled={!current}>
            {(current?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
      </div>
      <button onClick={() => void writePrompt()} disabled={busy || (!idea.trim() && !photo) || !endpointId || !model}>
        {busy ? "Writing…" : "Write the prompt"}
      </button>

      {prompt && (
        <>
          <label>Prompt (edit before rendering)
            <textarea rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <label>Render with
            <select value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
              <option value="">Choose a creator app</option>
              {creators.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <button onClick={() => void render()} disabled={!workflowId || !prompt.trim()}>Render two candidates</button>
        </>
      )}

      {renders.length > 0 && (
        <ul className="creator-renders" aria-label="Candidate renders">
          {renders.map((r) => (
            <li key={r.id}>
              Seed {r.seed}: {r.state.toLowerCase()}
              {r.state === "COMPLETED" && (
                <>
                  {" "}<img src={`/api/forge/runs/${r.id}/output`} alt={`Candidate, seed ${r.seed}`} width={160} />{" "}
                  <button onClick={() => void useAsAnchor(r.id)} disabled={!character}>Use as anchor</button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {!character && renders.some((r) => r.state === "COMPLETED") && <p className="muted">Choose a character above to use a render as its anchor.</p>}
    </section>
  );
}
