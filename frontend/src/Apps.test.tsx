import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Apps } from "./Apps";
import { forgeApi } from "./api";

// Only the Forge calls the Apps page makes are replaced. Everything else in ./api stays real.
vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    forgeApi: {
      ...real.forgeApi,
      list: vi.fn(),
      analysis: vi.fn(),
      templates: vi.fn(async () => ({ available: false, reason: "not in this test", templates: [] })),
    },
  };
});

const list = vi.mocked(forgeApi.list);

beforeEach(() => {
  list.mockReset();
});

afterEach(cleanup);

describe("Apps page", () => {
  it("shows the first-run start panel when there are no apps", async () => {
    list.mockResolvedValue({ workflows: [] } as unknown as Awaited<ReturnType<typeof forgeApi.list>>);
    render(<Apps />);
    expect(await screen.findByRole("heading", { name: "Start your first app" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import workflow" })).toBeTruthy();
  });

  it("shows an error banner when the list cannot be loaded", async () => {
    list.mockRejectedValue(new Error("backend is not running"));
    render(<Apps />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("backend is not running");
  });

  it("says 'Ready, with notes' when a model file is only installed in another folder", async () => {
    const wf = { id: "c".repeat(32), name: "Found elsewhere", imported_at: "2026-10-27T09:00:00Z", format: "ui", sha256: "2".repeat(64), size: 10, origin: "upload", readiness: "needs_attention", reasons: [] };
    list.mockResolvedValue({ workflows: [wf] } as unknown as Awaited<ReturnType<typeof forgeApi.list>>);
    vi.mocked(forgeApi.analysis).mockResolvedValue({
      analysis: {
        readiness: "needs_attention", reasons: [], counts: { controls: 3, model_files: 1 },
        model_files: [{ id: "m", node: "1", input: "x", class_type: "L", file: "a.safetensors", status: "found_elsewhere", comfy_name: "dir/a.safetensors", alternatives: [], checked_against: "live" }],
        dependencies: { counts: { missing: 0, choose: 0, unchecked: 0 } },
      },
    } as never);
    render(<Apps />);
    // Apps open into their own page; the readiness card is on the Edit app and Analysis tabs.
    fireEvent.click(await screen.findByRole("button", { name: /Found elsewhere/ }));
    fireEvent.click(screen.getByRole("tab", { name: "Edit app" }));
    expect(await screen.findByRole("heading", { name: "Ready, with notes" })).toBeTruthy();
    expect(screen.getByText(/installed in another folder/)).toBeTruthy();
  });

  it("opens an app into its own page with Use first, and goes back to the library", async () => {
    const wf = { id: "d".repeat(32), name: "Poster maker", imported_at: "2026-10-27T09:00:00Z", format: "ui", sha256: "4".repeat(64), size: 10, origin: "upload", readiness: "ready", reasons: [] };
    list.mockResolvedValue({ workflows: [wf] } as unknown as Awaited<ReturnType<typeof forgeApi.list>>);
    render(<Apps />);
    fireEvent.click(await screen.findByRole("button", { name: /Poster maker/ }));
    expect(screen.getByRole("tab", { name: "Use" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("navigation", { name: "Your apps" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "← All apps" }));
    expect(await screen.findByRole("navigation", { name: "Your apps" })).toBeTruthy();
  });

  it("lists saved apps by name", async () => {
    list.mockResolvedValue({
      workflows: [
        { id: "a".repeat(32), name: "Portrait studio", imported_at: "2026-10-27T09:00:00Z", format: "ui", sha256: "0".repeat(64), size: 10, origin: "upload" },
        { id: "b".repeat(32), name: "Landscape", imported_at: "2026-10-26T09:00:00Z", format: "api", sha256: "1".repeat(64), size: 10, origin: "upload" },
      ],
    } as unknown as Awaited<ReturnType<typeof forgeApi.list>>);
    vi.mocked(forgeApi.analysis).mockResolvedValue({} as never);
    render(<Apps />);
    await waitFor(() => expect(screen.getAllByText("Portrait studio").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Landscape").length).toBeGreaterThan(0);
  });
});

describe("Apps library", () => {
  const app = (id: string, name: string, readiness: string | null) => ({
    id: id.repeat(32), name, imported_at: `2026-10-2${id.charCodeAt(0) % 9}T09:00:00Z`, format: "ui", sha256: "3".repeat(64), size: 10, origin: "upload", readiness, reasons: [],
  });
  const three = [app("a", "Ready one", "ready"), app("b", "Attention two", "needs_attention"), app("c", "Blocked three", "blocked")];

  beforeEach(() => {
    list.mockResolvedValue({ workflows: three } as unknown as Awaited<ReturnType<typeof forgeApi.list>>);
    vi.mocked(forgeApi.analysis).mockResolvedValue({ analysis: { readiness: "ready", reasons: [], counts: { controls: 2, model_files: 0 }, model_files: [], dependencies: { counts: { missing: 0, choose: 0, unchecked: 0 } } } } as never);
  });

  it("shows a count on each status filter", async () => {
    render(<Apps />);
    const group = await screen.findByRole("group", { name: "Filter apps by status" });
    const chip = (label: string) => within(group).getByRole("button", { name: new RegExp(`^${label}`) });
    expect(chip("All").textContent).toContain("3");
    expect(chip("Ready").textContent).toContain("1");
    expect(chip("Needs attention").textContent).toContain("1");
    expect(chip("Blocked").textContent).toContain("1");
  });

  it("filters the list by status and marks the chosen filter as pressed", async () => {
    render(<Apps />);
    const group = await screen.findByRole("group", { name: "Filter apps by status" });
    fireEvent.click(within(group).getByRole("button", { name: /^Blocked/ }));
    expect(within(group).getByRole("button", { name: /^Blocked/ }).getAttribute("aria-pressed")).toBe("true");
    const nav = screen.getByRole("navigation", { name: "Your apps" });
    expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("Blocked three")]));
    expect(within(nav).queryByText("Ready one")).toBeNull();
  });

  it("switches between grid and list layouts", async () => {
    render(<Apps />);
    await screen.findByRole("navigation", { name: "Your apps" });
    const nav = screen.getByRole("navigation", { name: "Your apps" });
    expect(nav.className).not.toContain("lib-list");
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(nav.className).toContain("lib-list");
    expect(screen.getByRole("button", { name: "List" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("says when nothing matches and offers to show everything again", async () => {
    render(<Apps />);
    await screen.findByRole("navigation", { name: "Your apps" });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search your apps" }), { target: { value: "zzz" } });
    expect(await screen.findByText(/No apps match this filter and the name "zzz"/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show all apps" }));
    expect(screen.getByRole("navigation", { name: "Your apps" }).querySelectorAll(".lib-card").length).toBe(3);
  });
});
