import { useEffect, useRef, useState } from "react";
import { forgeApi, forgeOperatorApi, jobsApi, type OperatorCard, type OperatorEndpoint, type OperatorTurn } from "./api";

// The operator talks to a local model and proposes runs. A run card runs only when the user clicks Run,
// or when autopilot is on. Both paths call the same run route, and the job is then watched until it ends.

const STATE_LABEL: Record<OperatorEndpoint["state"], string> = { ok: "running", no_models: "no model loaded", unreachable: "not running" };
const JOB_DONE = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERY_REQUIRED"]);

interface Message { role: "user" | "assistant"; content: string; cards?: CardRun[] }
interface CardRun { card: OperatorCard; status: string; jobId?: string; error?: string }

export function Operator() {
  const [endpoints, setEndpoints] = useState<OperatorEndpoint[] | null>(null);
  const [endpointId, setEndpointId] = useState("");
  const [model, setModel] = useState("");
  const [autopilot, setAutopilot] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);

  useEffect(() => () => { live.current = false; }, []);

  const loadEndpoints = async () => {
    try {
      const { endpoints: list } = await forgeOperatorApi.endpoints();
      if (!live.current) return;
      setEndpoints(list);
      const ready = list.find((e) => e.state === "ok");
      setEndpointId((cur) => cur || ready?.id || "");
      setModel((cur) => cur || ready?.models[0] || "");
    } catch (e) { if (live.current) setError(e instanceof Error ? e.message : "Could not look for model servers."); }
  };
  useEffect(() => { void loadEndpoints(); }, []);

  const current = endpoints?.find((e) => e.id === endpointId) ?? null;

  // Watch one card's job until it ends. Returns the final state, or a message if it could not be watched.
  const watch = async (jobId: string): Promise<string> => {
    for (let i = 0; i < 900; i++) {
      const job = await jobsApi.get(jobId);
      if (JOB_DONE.has(job.state)) return job.state;
      await new Promise((r) => setTimeout(r, 2000));
      if (!live.current) return "stopped watching";
    }
    return "still running; check Jobs";
  };

  const runCard = async (card: OperatorCard, msgIndex: number, cardIndex: number) => {
    const set = (patch: Partial<CardRun>) => setMessages((ms) => ms.map((m, i) => i !== msgIndex ? m : {
      ...m, cards: m.cards?.map((c, j) => j === cardIndex ? { ...c, ...patch } : c) }));
    set({ status: "starting" });
    try {
      const { job } = await forgeApi.run(card.app_id, card.values, "local");
      set({ status: "running", jobId: job.id });
      const final = await watch(job.id);
      if (live.current) set({ status: final === "COMPLETED" ? "completed" : final.toLowerCase() });
    } catch (e) {
      if (live.current) set({ status: "not started", error: e instanceof Error ? e.message : "The run could not start." });
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || busy || !current || !model) return;
    setError(null);
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((ms) => [...ms, { role: "user", content: text }]);
    setDraft("");
    setBusy(true);
    try {
      const turn: OperatorTurn = await forgeOperatorApi.chat({ endpoint: current.id, model, message: text, history });
      if (!live.current) return;
      const cards: CardRun[] = turn.cards.map((card) => ({ card, status: autopilot ? "queued" : "waiting for you" }));
      // The user's message is already in the log, so the reply lands at the next index.
      const index = messages.length + 1;
      setMessages((ms) => [...ms, { role: "assistant", content: turn.answer, cards }]);
      // Autopilot runs the proposed cards in order, after the reply is on screen.
      if (autopilot) {
        for (let i = 0; i < turn.cards.length && live.current; i++) {
          await runCard(turn.cards[i], index, i);
        }
      }
    } catch (e) {
      if (live.current) setError(e instanceof Error ? e.message : "The OW agent could not answer.");
    } finally {
      if (live.current) setBusy(false);
    }
  };

  return (
    <section className="card operator" aria-labelledby="op-h">
      <header className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h2 id="op-h">OW agent</h2>
          <p className="muted">A chat that reads your apps and proposes runs. It runs on a model on this computer. It never runs an app without your Run click, unless autopilot is on.</p>
        </div>
        <button className="secondary" onClick={() => void loadEndpoints()}>Look for servers</button>
      </header>

      {endpoints && endpoints.every((e) => e.state !== "ok") && (
        <p className="banner" role="status">No local model server is running. Start one, load a chat model, then choose "Look for servers". Ollama runs on port 11434. In LM Studio, open the Developer tab and start the server (port 1234). llama.cpp (llama-server) uses port 8080.</p>
      )}

      <div className="operator-setup">
        <label>Model server
          <select value={endpointId} onChange={(e) => { setEndpointId(e.target.value); const next = endpoints?.find((x) => x.id === e.target.value); setModel(next?.models[0] ?? ""); }}>
            <option value="">Choose a server</option>
            {(endpoints ?? []).map((e) => <option key={e.id} value={e.id} disabled={e.state !== "ok"}>{e.label} ({STATE_LABEL[e.state]})</option>)}
          </select>
        </label>
        <label>Model
          <select value={model} onChange={(e) => setModel(e.target.value)} disabled={!current || current.models.length === 0}>
            {(current?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={autopilot} onChange={(e) => setAutopilot(e.target.checked)} />
          Autopilot: run the apps the OW agent proposes
        </label>
      </div>
      {autopilot && <p className="muted" role="note">Autopilot runs only ready apps, on this computer, and only when you send a message. Each run still shows its job.</p>}

      <div className="operator-log" role="log" aria-live="polite">
        {messages.length === 0 && <p className="muted">Try: "What apps do I have?" or "Make a fox in snow with the Z-Image app."</p>}
        {messages.map((m, i) => (
          <div key={i} className={`operator-msg ${m.role}`}>
            <p><strong>{m.role === "user" ? "You" : "OW agent"}:</strong> {m.content}</p>
            {m.cards?.map((c, j) => (
              <div key={j} className="operator-card">
                <p><strong>Run {c.card.app_name}</strong> · {c.status}{c.jobId ? ` · job ${c.jobId.slice(0, 8)}` : ""}</p>
                <ul>{Object.entries(c.card.values).map(([k, v]) => <li key={k}>{k}: {String(v)}</li>)}</ul>
                {c.card.rejected.length > 0 && <p className="muted">Not used: {c.card.rejected.join("; ")}</p>}
                {c.error && <p className="banner bad" role="alert">{c.error}</p>}
                {!autopilot && c.status === "waiting for you" && (
                  <button onClick={() => void runCard(c.card, i, j)}>Run {c.card.app_name}</button>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>

      {error && <div className="banner bad" role="alert">{error}</div>}

      <form className="operator-input" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label className="sr-only" htmlFor="op-input">Message the OW agent</label>
        <textarea id="op-input" rows={2} value={draft} maxLength={2000} onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about your apps, or ask for a run" disabled={busy} />
        <button type="submit" disabled={busy || !draft.trim() || !current || !model}>{busy ? "Thinking…" : "Send"}</button>
      </form>
    </section>
  );
}
