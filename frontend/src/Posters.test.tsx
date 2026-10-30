import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Posters } from "./Posters";
import { mossApi } from "./api";

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    mossApi: {
      ...real.mossApi,
      status: vi.fn(), create: vi.fn(), get: vi.fn(), list: vi.fn(),
      presets: vi.fn(), layout: vi.fn(), suggest: vi.fn(), suggestion: vi.fn(), aspects: vi.fn(),
    },
  };
});

const ready = { state: "ready", url: "http://127.0.0.1:8189", message: "The MOSS server is running on this computer." } as const;
const presetBoxes = [
  { type: "obj" as const, x: 0.2, y: 0.1, w: 0.6, h: 0.6, desc: "the product" },
  { type: "text" as const, x: 0.1, y: 0.78, w: 0.8, h: 0.14, desc: "headline", text: "" },
];
const done = {
  id: "d".repeat(32), state: "completed", created_at: "2026-10-28T09:00:00Z", finished_at: "2026-10-28T09:00:30Z",
  params: { prompt: "a mug", headline: "SUMMER SALE", width: 512, height: 512, seed: 1 },
  output: { name: "output.png", sha256: "x" }, receipt: "/r", message: null,
} as const;

beforeEach(() => {
  vi.mocked(mossApi.status).mockResolvedValue({ ...ready } as never);
  vi.mocked(mossApi.list).mockResolvedValue({ posters: [] });
  vi.mocked(mossApi.aspects).mockResolvedValue({ bases: [512, 1024], aspects: [
    { id: "square", name: "Square", use: "Feed post", sizes: { "512": [512, 512], "1024": [1024, 1024] } },
    { id: "story", name: "Story 9:16", use: "Stories", sizes: { "512": [512, 912], "1024": [1024, 1824] } },
  ] });
  vi.mocked(mossApi.presets).mockResolvedValue({ presets: [
    { id: "product_hero", name: "Product hero", description: "Product centred.", boxes: presetBoxes },
    { id: "sale_banner", name: "Sale banner", description: "Headline on top.", boxes: [{ type: "text", x: 0.08, y: 0.06, w: 0.84, h: 0.16, desc: "headline", text: "" }] },
  ] });
  vi.mocked(mossApi.layout).mockResolvedValue({ boxes: presetBoxes, layout: { items: [{ label: "object" }] }, message: null } as never);
  vi.mocked(mossApi.create).mockResolvedValue({ poster: { ...done, state: "running", output: null, receipt: null } as never });
  vi.mocked(mossApi.get).mockResolvedValue({ poster: { ...done } as never });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

/** Wait until the server status has loaded and a scene is typed, so the button is enabled. */
async function typeScene(text: string) {
  fireEvent.change(screen.getByLabelText(/Describe the product/), { target: { value: text } });
  await waitFor(() => expect((screen.getByRole("button", { name: "Generate poster" }) as HTMLButtonElement).disabled).toBe(false));
}

describe("Posters page", () => {
  it("says so and blocks generating when the MOSS server is not running", async () => {
    vi.mocked(mossApi.status).mockResolvedValue({ state: "not_running", url: "http://127.0.0.1:8189", message: "The MOSS server is not running. Start it, then try again." } as never);
    render(<Posters />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Describe the product/), { target: { value: "a mug" } });
    expect((screen.getByRole("button", { name: "Generate poster" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("needs a scene before it will generate", async () => {
    render(<Posters />);
    await screen.findByLabelText(/Describe the product/);
    expect((screen.getByRole("button", { name: "Generate poster" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("starts a poster with the scene, the size and the boxes, with the headline in the text box", async () => {
    render(<Posters />);
    await typeScene("a glass bottle");
    fireEvent.change(screen.getByLabelText(/Headline \(shortcut/), { target: { value: " NEW SCENT " } });
    fireEvent.click(await screen.findByRole("radio", { name: /Story/ }));
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    await waitFor(() => expect(mossApi.create).toHaveBeenCalled());
    const body = vi.mocked(mossApi.create).mock.calls[0][0];
    expect(body).toMatchObject({ prompt: "a glass bottle", aspect: "story", base: 512, seed: 1 });
    expect(body.boxes).toEqual(expect.arrayContaining([expect.objectContaining({ type: "text", text: " NEW SCENT " })]));
  });

  it("applies a layout preset and keeps the headline in its text box", async () => {
    render(<Posters />);
    await screen.findByRole("group", { name: "Layout presets" });
    fireEvent.change(screen.getByLabelText(/Headline \(shortcut/), { target: { value: "SALE" } });
    fireEvent.click(screen.getByRole("button", { name: "Sale banner" }));
    expect(document.querySelectorAll(".editor-stage .layout-box").length).toBe(1);
    expect((screen.getByLabelText(/Headline \(shortcut/) as HTMLInputElement).value).toBe("SALE");
  });

  it("shows the exact layout MOSS receives, and says when the layout is wrong", async () => {
    render(<Posters />);
    await screen.findByRole("group", { name: "Layout presets" });
    vi.mocked(mossApi.layout).mockRejectedValue(new Error("box 1 runs past the canvas edge"));
    fireEvent.change(screen.getByLabelText(/Headline \(shortcut/), { target: { value: "SALE" } });
    expect(await screen.findByText("box 1 runs past the canvas edge")).toBeTruthy();
  });

  it("asks for the words when a text box is empty, without calling the server", async () => {
    render(<Posters />);
    await screen.findByRole("group", { name: "Layout presets" });
    expect(await screen.findByText("Type the words for each text box, or remove the box.")).toBeTruthy();
    expect(vi.mocked(mossApi.layout).mock.calls.every((c) => c[0].boxes.every((b) => b.type !== "text" || (b.text ?? "").trim()))).toBe(true);
  });

  it("asks the MOSS text encoder for a prompt and fills the scene when it is written", async () => {
    vi.mocked(mossApi.suggest).mockResolvedValue({ request: { id: "r1", state: "running" } });
    vi.mocked(mossApi.suggestion).mockResolvedValue({ request: { id: "r1", state: "done", prompt: "A perfume bottle, rim light.", source: "moss_text_encoder", note: null } });
    render(<Posters />);
    await screen.findByRole("group", { name: "Layout presets" });
    fireEvent.change(screen.getByLabelText(/Describe the product/), { target: { value: "perfume" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Suggest a prompt" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Suggest a prompt" }));
    expect(await screen.findByText("Written by the MOSS text encoder", undefined, { timeout: 4000 })).toBeTruthy();
    expect((screen.getByLabelText(/Describe the product/) as HTMLTextAreaElement).value).toBe("A perfume bottle, rim light.");
  });

  it("explains when the helper used the template instead", async () => {
    vi.mocked(mossApi.suggest).mockResolvedValue({ request: { id: "r2", state: "done", prompt: "perfume, professional product advertisement", source: "template", note: "The MOSS server is not running. Used the plain template instead." } });
    render(<Posters />);
    fireEvent.change(screen.getByLabelText(/Describe the product/), { target: { value: "perfume" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Suggest a prompt" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Suggest a prompt" }));
    expect(await screen.findByText(/Used the plain template instead/)).toBeTruthy();
  });

  it("shows the finished poster with download links for the image and the receipt", async () => {
    vi.mocked(mossApi.list).mockResolvedValue({ posters: [{ id: done.id, state: "completed", created_at: done.created_at, headline: "SUMMER SALE", prompt: "a mug" }] });
    render(<Posters />);
    fireEvent.click(await screen.findByRole("button", { name: "Open" }));
    expect(await screen.findByRole("img", { name: "Poster with the headline SUMMER SALE" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download the image" }).hasAttribute("download")).toBe(true);
    expect(screen.getByRole("link", { name: "Download the receipt" }).hasAttribute("download")).toBe(true);
  });

  it("shows the failure reason when a poster fails", async () => {
    vi.mocked(mossApi.create).mockResolvedValue({ poster: { ...done, state: "failed", message: "out of memory" } as never });
    render(<Posters />);
    await typeScene("a mug");
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    expect(await screen.findByText("out of memory")).toBeTruthy();
  });
});
