import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgeCreatorApi, forgeOperatorApi } from "./api";
import { Creator } from "./Creator";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, forgeOperatorApi: { endpoints: vi.fn(), chat: vi.fn() }, forgeCreatorApi: { ...actual.forgeCreatorApi, prompt: vi.fn(), runs: vi.fn(), anchorFromRun: vi.fn() } };
});

beforeEach(() => {
  vi.mocked(forgeOperatorApi.endpoints).mockResolvedValue({ endpoints: [{ id: "ollama", label: "Ollama", base: "x", kind: "ollama", state: "ok", models: ["qwen3.5:4b"], reason: null }] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("Creator panel", () => {
  it("keeps the write button off until there is an idea or a photo", async () => {
    render(<Creator apps={[]} character={null} onAnchor={() => undefined} />);
    const btn = await screen.findByRole("button", { name: "Write the prompt" }) as HTMLButtonElement;
    await screen.findByDisplayValue("qwen3.5:4b");
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Idea"), { target: { value: "a calm nurse" } });
    expect((screen.getByRole("button", { name: "Write the prompt" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("offers only creator apps, and renders need a prompt", async () => {
    render(<Creator apps={[{ id: "a".repeat(32), name: "Krea2 creator remapped", origin: "upload", format: "api", sha256: "s", size: 1, imported_at: "", readiness: "ready" }, { id: "b".repeat(32), name: "Blur tool", origin: "upload", format: "api", sha256: "s", size: 1, imported_at: "", readiness: "ready" }] as never} character={null} onAnchor={() => undefined} />);
    await screen.findByDisplayValue("qwen3.5:4b");
    vi.mocked(forgeCreatorApi.prompt).mockResolvedValue({ prompt: "A portrait.", model: "qwen3.5:4b", reference_used: false });
    fireEvent.change(screen.getByLabelText("Idea"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Write the prompt" }));
    expect(await screen.findByLabelText("Prompt (edit before rendering)")).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Blur tool" })).toBeNull();
  });
});
