// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { RunReceipt } from "./RunReceipt";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const receipt = {
  job_id: "job1", workflow_name: "image_z_image_turbo", spec_revision: 1, provider: "local", values: { seed: 7 },
  output_sha256: "abc123", node_definitions: { comfyui_version: "0.43.0" }, app_version: "0.1.0",
  models: [{ role: "unet_name", file: "z_image.safetensors", status: "available" }], limitations: ["Seeds do not guarantee identical output across software or hardware versions."],
};

describe("RunReceipt", () => {
  it("shows only what the receipt holds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => receipt }));
    render(<RunReceipt jobId="job1" />);
    expect(await screen.findByText("abc123")).toBeTruthy();
    expect(screen.getByText("This computer (local ComfyUI)")).toBeTruthy();
    expect(screen.getByText("z_image.safetensors")).toBeTruthy();
    expect(screen.getByText(/Seeds do not guarantee/)).toBeTruthy();
  });

  it("says so when the receipt cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    render(<RunReceipt jobId="job1" />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be read/);
  });
});
