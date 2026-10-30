import { describe, expect, it } from "vitest";
import { buildStages, emptyStep, stepProblems, type FlowStep } from "./Flow";

const port = (name: string, type: "image" | "text") => ({
  name, type, node: "1", field: "x", label: name, default: null, required: type === "image", options: null,
});

function step(over: Partial<FlowStep>): FlowStep {
  return { ...emptyStep(), appId: "app", ...over };
}

describe("flow steps", () => {
  it("asks for an app before anything else", () => {
    expect(stepProblems(emptyStep(), 0, [emptyStep()])).toEqual(["Choose an app for this step."]);
  });

  it("needs an uploaded image when nothing feeds the step", () => {
    const s = step({ ports: [port("image", "image")] });
    expect(stepProblems(s, 0, [s])).toEqual(["\"image\" needs an uploaded image."]);
  });

  it("takes the previous image result instead of an upload", () => {
    const first = step({ output: { node: "9", media: "image" } });
    const second = step({ ports: [port("image", "image")], feedPrevious: true });
    expect(stepProblems(second, 1, [first, second])).toEqual([]);
    const stages = buildStages([first, second]);
    expect(stages[1].values).toEqual({ image: { from_stage: "s1", output: "result" } });
  });

  it("does not feed a video result into an image input", () => {
    const first = step({ output: { node: "9", media: "video" } });
    const second = step({ ports: [port("image", "image")], images: { image: "openfield_abc.png" } });
    expect(stepProblems(second, 1, [first, second])).toEqual([]);
    expect(buildStages([first, second])[1].values).toEqual({ image: "openfield_abc.png" });
  });

  it("names each stage in order", () => {
    const stages = buildStages([step({}), step({})]);
    expect(stages.map((s) => s.id)).toEqual(["s1", "s2"]);
  });
});
