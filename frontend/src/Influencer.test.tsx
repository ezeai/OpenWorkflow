import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SpecComponent } from "./api";
import { forgeApi, forgeCharacterApi, jobsApi } from "./api";
import { Influencer, percentOf, sceneControls, stageOf } from "./Influencer";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    forgeApi: { ...actual.forgeApi, list: vi.fn(), spec: vi.fn(), run: vi.fn() },
    forgeCharacterApi: { ...actual.forgeCharacterApi, list: vi.fn(), get: vi.fn(), create: vi.fn(), setAnchor: vi.fn(), check: vi.fn() },
    jobsApi: { ...actual.jobsApi, get: vi.fn() },
  };
});

function comp(over: Partial<SpecComponent> & Pick<SpecComponent, "id" | "type">): SpecComponent {
  return { label: over.id, help: "", group: "main", hidden: false, role: null, binding: { node: "1", input: "v" }, props: {}, ...over } as SpecComponent;
}

describe("sceneControls", () => {
  it("finds the image input, the scene prompt and the seed", () => {
    expect(sceneControls([
      comp({ id: "photo", type: "image_upload", label: "Input image" }),
      comp({ id: "neg", type: "prompt", label: "Negative prompt" }),
      comp({ id: "pos", type: "prompt", label: "Prompt" }),
      comp({ id: "seed", type: "seed", label: "Seed" }),
    ])).toEqual({ image: "photo", prompt: "pos", seed: "seed" });
  });

  it("never picks a negative prompt, even when it is the only prompt", () => {
    expect(sceneControls([comp({ id: "neg", type: "prompt", label: "Prompt (x)", binding: { node: "1", input: "negative_prompt" } })]).prompt).toBeNull();
  });

  it("reports missing controls as null, and ignores advanced and hidden ones", () => {
    expect(sceneControls([
      comp({ id: "photo", type: "image_upload", group: "advanced" }),
      comp({ id: "seed", type: "seed", hidden: true }),
    ])).toEqual({ image: null, prompt: null, seed: null });
  });
});

describe("progress helpers", () => {
  it("turns a determinate progress record into a percentage", () => {
    expect(percentOf({ mode: "determinate", step: 1, steps: 4 } as never)).toBe(25);
    expect(percentOf({ mode: "indeterminate" } as never)).toBeNull();
    expect(percentOf(null)).toBeNull();
  });

  it("names the stage a render is in from its ComfyUI node", () => {
    expect(stageOf("QUEUED", null)).toBe("Queued");
    expect(stageOf("RUNNING", "CLIPTextEncode")).toBe("Encoding prompt");
    expect(stageOf("RUNNING", "KSampler")).toBe("Sampling");
    expect(stageOf("RUNNING", "VAEDecode")).toBe("Decoding");
    expect(stageOf("RUNNING", "SaveImage")).toBe("Saving");
    expect(stageOf("POSTPROCESSING", "SaveImage")).toBe("Saving");
  });
});

const character = { id: "c".repeat(32), name: "Maya", notes: "", created_at: "2026-10-29T10:00:00+00:00", anchor: null, history: [] };
const anchored = { ...character, anchor: { comfy_name: "openfield_0123456789abcdef.png", path: "x", sha256: "s", width: 512, height: 512, original_name: "face.png" } };
const sceneApp = { id: "w".repeat(32), name: "Scene", origin: "upload", format: "api", sha256: "s", size: 1, imported_at: "", readiness: "ready" };

beforeEach(() => {
  vi.mocked(forgeApi.list).mockResolvedValue({ workflows: [] } as never);
  vi.mocked(forgeCharacterApi.list).mockResolvedValue({ characters: [{ id: character.id, name: "Maya", created_at: character.created_at, anchor: false, checks: 0 }] });
  vi.mocked(forgeCharacterApi.get).mockResolvedValue({ character });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AI Influencer tab", () => {
  it("shows the empty anchor state and disables scenes until there is an anchor", async () => {
    render(<Influencer />);
    fireEvent.click(await screen.findByRole("button", { name: /Maya/ }));
    expect(await screen.findByText("No anchor yet")).toBeTruthy();
    expect(screen.getByText("Add one clear photo of one face (PNG, JPEG or WebP). Everything else uses it.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Next: Anchor.");
    expect(screen.getByRole("button", { name: /Sheet/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Scenes" }));
    expect((screen.getByRole("button", { name: "Generate scene" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("runs a scene with the anchor, follows progress to done, and checks the face", async () => {
    vi.mocked(forgeCharacterApi.get).mockResolvedValue({ character: anchored });
    vi.mocked(forgeCharacterApi.list).mockResolvedValue({ characters: [{ id: character.id, name: "Maya", created_at: character.created_at, anchor: true, checks: 0 }] });
    vi.mocked(forgeApi.list).mockResolvedValue({ workflows: [sceneApp] } as never);
    vi.mocked(forgeApi.spec).mockResolvedValue({ spec: { components: [
      comp({ id: "photo", type: "image_upload" }), comp({ id: "prompt", type: "prompt" }), comp({ id: "seed", type: "seed" }),
    ], outputs: [] } } as never);
    vi.mocked(forgeApi.run).mockResolvedValue({ job: { id: "j".repeat(32), state: "QUEUED", notes: [], provider: "local" } });
    vi.mocked(jobsApi.get).mockResolvedValue({ state: "COMPLETED", progress: { mode: "determinate", step: 4, steps: 4, nodes_finished: 9 } } as never);
    vi.mocked(forgeCharacterApi.check).mockResolvedValue({
      check: { score: 0.61, threshold: 0.35, passed: true, face_found: true, job_id: "j".repeat(32), at: "now" },
      character: { ...anchored, history: [{ score: 0.61, threshold: 0.35, passed: true, face_found: true, job_id: "j".repeat(32), at: "now" }] },
    });

    render(<Influencer />);
    fireEvent.click(await screen.findByRole("button", { name: /Maya/ }));
    fireEvent.click(await screen.findByRole("tab", { name: "Scenes" }));
    fireEvent.change(screen.getByLabelText("App to use"), { target: { value: "w".repeat(32) } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Generate scene" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Generate scene" }));
    await waitFor(() => expect(forgeApi.run).toHaveBeenCalledWith("w".repeat(32), expect.objectContaining({ photo: "openfield_0123456789abcdef.png", prompt: "", seed: 42 }), "local"));

    fireEvent.click(await screen.findByRole("button", { name: "Check face" }));
    expect(await screen.findByText("similarity 0.61")).toBeTruthy();
    expect(screen.getByText(/not proof of identity/)).toBeTruthy();
  });
});
