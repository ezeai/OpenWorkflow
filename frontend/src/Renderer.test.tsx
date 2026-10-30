import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Renderer, defaultValues } from "./Renderer";
import type { ComponentVerdict, SpecComponent, UISpec } from "./api";

function comp(over: Partial<SpecComponent> & { id: string; type: SpecComponent["type"] }): SpecComponent {
  return {
    label: over.id, help: "", binding: { node: "1", input: "x" }, group: "main", hidden: false, role: null,
    props: { min: null, max: null, step: null, default: null, choices: null, rows: null, placeholder: null },
    ...over,
  } as SpecComponent;
}

function spec(layout: UISpec["layout"], components: SpecComponent[]): UISpec {
  return { schema_version: 1, name: "Demo app", description: "", workflow_id: "w", workflow_sha256: "0", layout,
    components, outputs: [{ node: "9", media: "image", label: "Image" }], generated_by: "deterministic" } as UISpec;
}

const ok = (id: string): ComponentVerdict => ({ component: id, status: "verified", reasons: [] } as unknown as ComponentVerdict);

const prompt = comp({ id: "p", type: "prompt", label: "Prompt" });
const seed = comp({ id: "s", type: "seed", label: "Seed", group: "advanced" });
const person = comp({ id: "img1", type: "image_upload", label: "Person photo", role: "identity" });
const pose = comp({ id: "img2", type: "image_upload", label: "Pose photo", role: "layout" });

function renderIt(s: UISpec, extra: Partial<Parameters<typeof Renderer>[0]> = {}) {
  const verdicts = s.components.map((c) => ok(c.id));
  return render(<Renderer spec={s} verdicts={verdicts} values={defaultValues(s)} onChange={() => undefined} {...extra} />);
}

afterEach(cleanup);

describe("defaultValues", () => {
  it("uses spec defaults and sets empty text and false toggles", () => {
    const s = spec("simple_generator", [
      comp({ id: "n", type: "number", props: { min: null, max: null, step: null, default: 512, choices: null, rows: null, placeholder: null } }),
      prompt,
      comp({ id: "t", type: "toggle" }),
    ]);
    expect(defaultValues(s)).toEqual({ n: 512, p: "", t: false });
  });
});

describe("Renderer layouts", () => {
  it("reference_guided puts the References group before the prompt", () => {
    renderIt(spec("reference_guided", [prompt, person, pose]));
    const refs = screen.getByRole("group", { name: "References" });
    const promptBox = screen.getByLabelText("Prompt");
    expect(refs.compareDocumentPosition(promptBox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("labels each reference image with its role", () => {
    renderIt(spec("reference_guided", [person, pose, prompt]));
    expect(screen.getByText("Person or character")).toBeTruthy();
    expect(screen.getByText("Layout or pose")).toBeTruthy();
  });

  it("simple_generator keeps advanced settings collapsed", () => {
    const { container } = renderIt(spec("simple_generator", [prompt, seed]));
    const details = container.querySelector("details.r-advanced") as HTMLDetailsElement;
    expect(details).toBeTruthy();
    expect(details.open).toBe(false);
  });

  it("advanced_workspace shows settings and output side by side with the advanced group open", () => {
    const { container } = renderIt(spec("advanced_workspace", [prompt, seed]));
    expect(container.querySelector(".r-workspace .r-settings")).toBeTruthy();
    expect(container.querySelector(".r-workspace .r-side")).toBeTruthy();
    expect((container.querySelector("details.r-advanced") as HTMLDetailsElement).open).toBe(true);
    expect(screen.getByRole("heading", { name: "Main settings" })).toBeTruthy();
  });

  it("every layout shows the output region", () => {
    for (const layout of ["simple_generator", "reference_guided", "advanced_workspace"] as const) {
      const { unmount } = renderIt(spec(layout, [prompt]));
      expect(screen.getByRole("region", { name: "Output" })).toBeTruthy();
      unmount();
    }
  });
});

describe("Renderer actions and states", () => {
  it("calls onRun when Generate is pressed", () => {
    const onRun = vi.fn();
    renderIt(spec("simple_generator", [prompt]), { onRun });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("disables Generate and shows the reason when the run is blocked", () => {
    renderIt(spec("simple_generator", [prompt]), { onRun: () => undefined, runReason: "Start ComfyUI first." });
    expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Start ComfyUI first.")).toBeTruthy();
  });

  it("does not draw a component whose binding is invalid", () => {
    const s = spec("simple_generator", [prompt, comp({ id: "bad", type: "text", label: "Hidden trap" })]);
    render(<Renderer spec={s} verdicts={[ok("p"), { component: "bad", status: "invalid", reasons: ["no such input"] } as unknown as ComponentVerdict]}
      values={defaultValues(s)} onChange={() => undefined} />);
    expect(screen.queryByText("Hidden trap")).toBeNull();
  });
});
