import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Apps } from "./Apps";
import { forgeApi, type ForgeTemplate } from "./api";

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    forgeApi: {
      ...real.forgeApi,
      list: vi.fn(async () => ({ workflows: [] })),
      analysis: vi.fn(),
      templates: vi.fn(),
    },
  };
});

const template = (name: string, title: string, group: string | null): ForgeTemplate => ({
  name, title, category: "Foundation", group, description: "", models: [], media: null, open_source: true, uses_paid_api: false,
});

beforeEach(() => {
  vi.mocked(forgeApi.templates).mockResolvedValue({
    available: true,
    templates: [
      template("img", "Portrait upscale", "Image"),
      template("vid", "Looping clip", "Video"),
      template("odd", "Odd one", "LLM"),
    ],
  } as unknown as Awaited<ReturnType<typeof forgeApi.templates>>);
});

afterEach(cleanup);

describe("Apps template mode tabs", () => {
  it("shows every template under All and only the matching mode under its tab", async () => {
    render(<Apps />);
    expect(await screen.findByText("Portrait upscale")).toBeTruthy();
    expect(screen.getByText("Odd one")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /^Video/ }));
    expect(screen.getByText("Looping clip")).toBeTruthy();
    expect(screen.queryByText("Portrait upscale")).toBeNull();
    expect(screen.queryByText("Odd one")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /^Other/ }));
    expect(screen.getByText("Odd one")).toBeTruthy();
  });
});
