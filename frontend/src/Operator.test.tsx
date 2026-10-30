import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgeApi, forgeOperatorApi, jobsApi } from "./api";
import { Operator } from "./Operator";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    forgeOperatorApi: { endpoints: vi.fn(), chat: vi.fn() },
    forgeApi: { ...actual.forgeApi, run: vi.fn() },
    jobsApi: { ...actual.jobsApi, get: vi.fn() },
  };
});

const ready = {
  id: "ollama", label: "Ollama", base: "http://127.0.0.1:11434", kind: "ollama" as const,
  state: "ok" as const, models: ["qwen3.5:4b"], reason: null,
};
const down = { ...ready, id: "lmstudio", label: "LM Studio", state: "unreachable" as const, models: [], reason: "not running" };

const card = {
  kind: "run_app" as const, app_id: "zimg", app_name: "Z-Image", values: { prompt: "a fox in snow" }, rejected: [],
};

beforeEach(() => {
  vi.mocked(forgeOperatorApi.endpoints).mockResolvedValue({ endpoints: [ready, down] });
  vi.mocked(forgeOperatorApi.chat).mockResolvedValue({ answer: "Ready: click Run.", cards: [card], trace: [] });
  vi.mocked(forgeApi.run).mockResolvedValue({ job: { id: "job-12345678", state: "QUEUED", notes: [], provider: "local" } });
  vi.mocked(jobsApi.get).mockResolvedValue({ state: "COMPLETED" } as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function send(text: string) {
  fireEvent.change(await screen.findByLabelText("Message the OW agent"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("OW agent panel", () => {
  it("says what to do when no model server is running", async () => {
    vi.mocked(forgeOperatorApi.endpoints).mockResolvedValue({ endpoints: [{ ...down, id: "ollama", label: "Ollama" }] });
    render(<Operator />);
    expect(await screen.findByText(/No local model server is running/)).toBeTruthy();
  });

  it("shows a proposed run as a card and does not run it until Run is clicked", async () => {
    render(<Operator />);
    await send("Make a fox in snow");
    expect(await screen.findByText("Ready: click Run.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run Z-Image" })).toBeTruthy();
    expect(forgeApi.run).not.toHaveBeenCalled();
    expect(forgeOperatorApi.chat).toHaveBeenCalledWith(expect.objectContaining({ endpoint: "ollama", model: "qwen3.5:4b", message: "Make a fox in snow" }));
  });

  it("runs the card through the local run route when Run is clicked, then watches the job", async () => {
    render(<Operator />);
    await send("Make a fox in snow");
    fireEvent.click(await screen.findByRole("button", { name: "Run Z-Image" }));
    await waitFor(() => expect(forgeApi.run).toHaveBeenCalledWith("zimg", { prompt: "a fox in snow" }, "local"));
    expect(await screen.findByText(/completed/)).toBeTruthy();
  });

  it("with autopilot on, runs the proposed card without a click, locally, and only that card", async () => {
    render(<Operator />);
    fireEvent.click(screen.getByLabelText(/Autopilot/));
    await send("Make a fox in snow");
    await waitFor(() => expect(forgeApi.run).toHaveBeenCalledTimes(1));
    expect(forgeApi.run).toHaveBeenCalledWith("zimg", { prompt: "a fox in snow" }, "local");
    expect(screen.queryByRole("button", { name: "Run Z-Image" })).toBeNull();
  });

  it("explains rejected values on the card", async () => {
    vi.mocked(forgeOperatorApi.chat).mockResolvedValue({
      answer: "Done.", cards: [{ ...card, rejected: ["Steps: expects a number"] }], trace: [],
    });
    render(<Operator />);
    await send("x");
    expect(await screen.findByText(/Not used: Steps: expects a number/)).toBeTruthy();
  });
});
