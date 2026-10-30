import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Renderer, defaultValues } from "./Renderer";
import type { ComponentVerdict, UISpec } from "./api";

const spec = {
  schema_version: 1, name: "Reasons", description: "", workflow_id: "a".repeat(32), workflow_sha256: "b".repeat(64),
  layout: "simple_generator", outputs: [], generated_by: "deterministic", rules: "deterministic-1",
  components: [
    { id: "prompt", type: "prompt", label: "Prompt", help: "CLIPTextEncode · text", group: "main", hidden: false,
      reason: "A prompt: text reaches a sampler's positive input.", binding: { node: "6", input: "text" }, props: { default: "" } },
    { id: "steps", type: "number", label: "Steps", help: "KSampler · steps", group: "main", hidden: false,
      binding: { node: "3", input: "steps" }, props: { default: 20 } },
  ],
} as unknown as UISpec;

const ok = (id: string): ComponentVerdict => ({ component: id, status: "verified", reasons: [], evidence: null } as unknown as ComponentVerdict);

afterEach(cleanup);

describe("Renderer shows why each control exists", () => {
  it("shows the reason in the builder preview, and none for an older spec without one", () => {
    render(<Renderer spec={spec} verdicts={[ok("prompt"), ok("steps")]} values={defaultValues(spec)} onChange={() => undefined} />);
    expect(screen.getByText("Why: A prompt: text reaches a sampler's positive input.")).toBeTruthy();
    expect(screen.getAllByText(/^Why:/)).toHaveLength(1);
  });

  it("keeps the studio plain: no reasons in compact mode", () => {
    render(<Renderer spec={spec} verdicts={[ok("prompt"), ok("steps")]} values={defaultValues(spec)} onChange={() => undefined} compact />);
    expect(screen.queryByText(/^Why:/)).toBeNull();
  });
});
