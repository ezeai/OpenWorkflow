import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RunHistory } from "./AppBuilder";
import { forgeRunsApi } from "./api";

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, forgeRunsApi: { ...real.forgeRunsApi, list: vi.fn() } };
});

const runs = [
  { id: "a".repeat(32), state: "COMPLETED", provider: "local", created_at: "2026-10-27T10:00:00Z", has_output: true, error: null, receipt_url: "/api/forge/runs/aaa/receipt" },
  { id: "b".repeat(32), state: "FAILED", provider: "local", created_at: "2026-10-27T09:00:00Z", has_output: false, error: "ComfyUI stopped", receipt_url: null },
];

beforeEach(() => {
  vi.mocked(forgeRunsApi.list).mockResolvedValue({ runs } as unknown as Awaited<ReturnType<typeof forgeRunsApi.list>>);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunHistory", () => {
  it("shows a thumbnail only for a completed run that has an output", async () => {
    render(<RunHistory workflowId="w" refreshKey="" />);
    const button = await screen.findByRole("button", { name: /Open the output of the run from/ });
    expect(button.querySelector("img")?.getAttribute("src")).toBe(`/api/forge/runs/${"a".repeat(32)}/output`);
    expect(document.querySelectorAll("img")).toHaveLength(1);
  });

  it("shows the failure reason for a failed run", async () => {
    render(<RunHistory workflowId="w" refreshKey="" />);
    expect(await screen.findByText("ComfyUI stopped")).toBeTruthy();
  });

  it("offers 'Use these values' only when a receipt exists, and passes the receipt's values on", async () => {
    const onReuse = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ values: { "57:27.text": "dusk", "57:3.seed": 42, junk: { nested: true } } }), { status: 200 })));
    render(<RunHistory workflowId="w" refreshKey="" onReuse={onReuse} />);
    const button = await screen.findByRole("button", { name: "Use these values" });
    expect(screen.getAllByRole("button", { name: "Use these values" })).toHaveLength(1);
    fireEvent.click(button);
    await waitFor(() => expect(onReuse).toHaveBeenCalledTimes(1));
    expect(onReuse).toHaveBeenCalledWith({ "57:27.text": "dusk", "57:3.seed": 42, junk: { nested: true } });
  });

  it("reports that nothing could be read when the receipt is missing, instead of guessing", async () => {
    const onReuse = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 404 })));
    render(<RunHistory workflowId="w" refreshKey="" onReuse={onReuse} />);
    fireEvent.click(await screen.findByRole("button", { name: "Use these values" }));
    await waitFor(() => expect(onReuse).toHaveBeenCalledWith({}));
  });
});

describe("Lightbox and run output actions", () => {
  it("is a labelled dialog that closes on Escape and on Close", async () => {
    const { Lightbox } = await import("./AppBuilder");
    const onClose = vi.fn();
    render(<Lightbox src="/out.png" label="Run from today" onClose={onClose} />);
    expect(screen.getByRole("dialog", { name: "Output: Run from today" })).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("offers a download link for the image and for the receipt", async () => {
    render(<RunHistory workflowId="w" refreshKey="" />);
    const img = await screen.findByRole("link", { name: "Download image" });
    expect(img.hasAttribute("download")).toBe(true);
    const receipt = screen.getByRole("link", { name: "Download receipt" });
    expect(receipt.hasAttribute("download")).toBe(true);
  });

  it("opens the full-size view when a completed run's thumbnail is pressed", async () => {
    const onExpand = vi.fn();
    render(<RunHistory workflowId="w" refreshKey="" onExpand={onExpand} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open the output of the run from/ }));
    expect(onExpand).toHaveBeenCalledWith(expect.objectContaining({ src: `/api/forge/runs/${"a".repeat(32)}/output` }));
  });
});
