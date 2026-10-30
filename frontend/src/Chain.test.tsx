import { describe, expect, it } from "vitest";
import type { SpecComponent, SpecView } from "./api";
import { planStep } from "./Chain";

function comp(over: Partial<SpecComponent> & Pick<SpecComponent, "id" | "type">): SpecComponent {
  return {
    label: over.id, help: "", group: "main", hidden: false, role: null,
    binding: { node: "1", input: "value" }, props: {}, ...over,
  } as SpecComponent;
}

function view(components: SpecComponent[], outputs: SpecView["spec"]["outputs"] = [{ node: "9", media: "image", label: "Image" }]): SpecView {
  return {
    spec: { schema_version: 1, name: "t", description: "", workflow_id: "w", workflow_sha256: "s", layout: "simple_generator", components, outputs, generated_by: "deterministic" },
    revision: 1, source: "generated", stale: false, readiness: "ready", reasons: [], catalog: [], layouts: [],
    validation: { status: "valid", counts: {}, components: [], problems: [], runnable: true },
  };
}

describe("planStep", () => {
  it("turns a text-to-image app into a first step with its prompt and seed as inputs", () => {
    const plan = planStep(view([
      comp({ id: "prompt", type: "prompt", binding: { node: "57:27", input: "text" }, props: { default: "a lighthouse" } }),
      comp({ id: "seed", type: "seed", binding: { node: "57:3", input: "seed" }, props: { default: 7 } }),
    ]), "first");
    expect(plan.problems).toEqual([]);
    expect(plan.outputNode).toBe("9");
    expect(plan.imageInput).toBeNull();
    expect(plan.ports.map((p) => [p.name, p.type, p.default])).toEqual([["prompt", "text", "a lighthouse"], ["seed", "integer", 7]]);
  });

  it("refuses a first step that needs its own image", () => {
    const plan = planStep(view([comp({ id: "photo", type: "image_upload", label: "Your photo" })]), "first");
    expect(plan.problems[0]).toMatch(/needs its own image/);
  });

  it("finds the image input of a second step and binds it to the first step's result", () => {
    const plan = planStep(view([
      comp({ id: "photo", type: "image_upload", label: "Input image", binding: { node: "1", input: "image" } }),
      comp({ id: "strength", type: "slider", binding: { node: "4", input: "denoise" }, props: { default: 0.5 } }),
    ]), "second");
    expect(plan.problems).toEqual([]);
    expect(plan.imageInput).toBe("photo");
    expect(plan.ports.find((p) => p.name === "photo")).toMatchObject({ type: "image", node: "1", field: "image", required: true });
    expect(plan.ports.find((p) => p.name === "strength")).toMatchObject({ type: "number", default: 0.5 });
  });

  it("explains a second step without an image input in plain words", () => {
    const plan = planStep(view([comp({ id: "prompt", type: "prompt" })]), "second");
    expect(plan.problems).toEqual(["This app has no image input to receive the first step's result."]);
  });

  it("says when an app does not save an image", () => {
    const plan = planStep(view([comp({ id: "prompt", type: "prompt" })], []), "first");
    expect(plan.problems).toEqual(["This app does not save an image, so its result cannot feed a second step."]);
  });

  it("leaves advanced and hidden controls out of the chain", () => {
    const plan = planStep(view([
      comp({ id: "fine", type: "number", group: "advanced" }),
      comp({ id: "gone", type: "number", hidden: true }),
      comp({ id: "prompt", type: "prompt" }),
    ]), "first");
    expect(plan.ports.map((p) => p.name)).toEqual(["prompt"]);
  });

  it("gives colliding names distinct port names", () => {
    const plan = planStep(view([
      comp({ id: "Prompt", type: "prompt" }),
      comp({ id: "prompt", type: "prompt" }),
    ]), "first");
    expect(plan.ports.map((p) => p.name)).toEqual(["prompt", "prompt_2"]);
  });
});
