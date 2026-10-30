import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LayoutEditor } from "./LayoutEditor";
import type { PosterBox } from "./api";

const two: PosterBox[] = [
  { type: "obj", x: 0.2, y: 0.1, w: 0.6, h: 0.6, desc: "the product" },
  { type: "text", x: 0.1, y: 0.8, w: 0.8, h: 0.14, desc: "headline", text: "SALE" },
];


/** Controlled wrapper, so each change is applied the way the page applies it. */
function Harness({ initial, warnings = [], onSpy }: { initial: PosterBox[]; warnings?: string[][]; onSpy: (b: PosterBox[]) => void }) {
  const [boxes, setBoxes] = useState(initial);
  return (
    <LayoutEditor boxes={boxes} warnings={warnings} tool="obj" onTool={() => undefined} ratio={1}
      onChange={(next: PosterBox[]) => { onSpy(next); setBoxes(next); }} />
  );
}

// jsdom has no PointerEvent. Give it a small one that keeps coordinates, so fireEvent.pointer* carries them.
class PointerEventPolyfill extends MouseEvent {
  pointerId: number;
  constructor(type: string, init: MouseEventInit & { pointerId?: number } = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}
(window as unknown as { PointerEvent: typeof PointerEventPolyfill }).PointerEvent = PointerEventPolyfill;

beforeEach(() => {
  // jsdom has no layout or pointer capture; give the stage a 100 x 100 box.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON() { return {}; } } as DOMRect);
  HTMLElement.prototype.setPointerCapture = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("LayoutEditor", () => {
  it("selects a box on click and shows its inspector", () => {
    render(<Harness initial={two} onSpy={() => undefined} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 30, pointerId: 1 });
    expect(screen.getByRole("group", { name: "Box type" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "What to draw next" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Product" }) as HTMLButtonElement).getAttribute("aria-pressed")).toBe("true");
  });

  it("Delete removes the selected box and Escape clears the selection", () => {
    const spy = vi.fn();
    render(<Harness initial={two} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="1"]') as Element, { clientX: 50, clientY: 90, pointerId: 1 });
    fireEvent.keyDown(screen.getByRole("application"), { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Box type" })).toBeNull();
    fireEvent.pointerDown(document.querySelector('[data-box="1"]') as Element, { clientX: 50, clientY: 90, pointerId: 1 });
    fireEvent.keyDown(screen.getByRole("application"), { key: "Delete" });
    expect(spy).toHaveBeenLastCalledWith([two[0]]);
  });

  it("arrow keys nudge the selected box by 1 percent, and by 5 with Shift", () => {
    const spy = vi.fn();
    render(<Harness initial={two} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 30, pointerId: 1 });
    fireEvent.keyDown(screen.getByRole("application"), { key: "ArrowRight" });
    expect(spy.mock.calls.at(-1)?.[0][0].x).toBeCloseTo(0.21);
    fireEvent.keyDown(screen.getByRole("application"), { key: "ArrowDown", shiftKey: true });
    expect(spy.mock.calls.at(-1)?.[0][0].y).toBeCloseTo(0.15);
  });

  it("dragging on empty space draws a new box of the chosen type", () => {
    const spy = vi.fn();
    render(<Harness initial={[]} onSpy={spy} />);
    const stage = screen.getByRole("application");
    fireEvent.pointerDown(stage, { clientX: 10, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 70, clientY: 80, pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 70, clientY: 80, pointerId: 1 });
    const last = spy.mock.calls.at(-1)?.[0][0];
    expect(last.type).toBe("obj");
    expect(last.x).toBeCloseTo(0.1);
    expect(last.y).toBeCloseTo(0.2);
    expect(last.w).toBeCloseTo(0.6);
    expect(last.h).toBeCloseTo(0.6);
  });

  it("a click without a drag does not leave an empty box behind", () => {
    const spy = vi.fn();
    render(<Harness initial={[]} onSpy={spy} />);
    const stage = screen.getByRole("application");
    fireEvent.pointerDown(stage, { clientX: 10, clientY: 20, pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 10, clientY: 20, pointerId: 1 });
    expect(spy.mock.calls.at(-1)?.[0]).toEqual([]);
  });

  it("switching a box to Text shows the exact-words field", () => {
    const spy = vi.fn();
    render(<Harness initial={[two[0]]} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 30, pointerId: 1 });
    expect(screen.queryByLabelText(/Words to render exactly/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(spy.mock.calls.at(-1)?.[0][0].type).toBe("text");
    expect(screen.getByLabelText(/Words to render exactly/)).toBeTruthy();
  });

  it("edits the exact words of a text box", () => {
    const spy = vi.fn();
    render(<Harness initial={[two[1]]} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 90, pointerId: 1 });
    fireEvent.change(screen.getByLabelText(/Words to render exactly/), { target: { value: "NEW SCENT" } });
    expect(spy.mock.calls.at(-1)?.[0][0].text).toBe("NEW SCENT");
  });

  it("shows the warnings the layout check returned, with the box number", () => {
    render(<Harness initial={two} warnings={[[], ["more than 5 words in one text box; long wording garbles"]]} onSpy={() => undefined} />);
    expect(screen.getByRole("list", { name: "Things to check before generating" }).textContent).toContain("Box 2:");
    expect(screen.getByText(/long wording garbles/)).toBeTruthy();
  });

  it("duplicates and reorders the selected box", () => {
    const spy = vi.fn();
    render(<Harness initial={two} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 30, pointerId: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Move later" }));
    expect(spy.mock.calls.at(-1)?.[0].map((b: PosterBox) => b.type)).toEqual(["text", "obj"]);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(spy.mock.calls.at(-1)?.[0].length).toBe(3);
  });

  it("sets colour codes on the selected box and refuses a bad one", () => {
    const spy = vi.fn();
    render(<Harness initial={[two[0]]} onSpy={spy} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 30, pointerId: 1 });
    const field = screen.getByLabelText(/Colours/) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "c8102e, #f5f1e6" } });
    expect(spy.mock.calls.at(-1)?.[0][0].palette).toEqual(["#C8102E", "#F5F1E6"]);
    fireEvent.change(field, { target: { value: "red" } });
    expect(screen.getByText(/Use up to five colour codes/)).toBeTruthy();
  });

  it("gives the character budget and word count for a text box", () => {
    render(<Harness initial={[two[1]]} onSpy={() => undefined} />);
    fireEvent.pointerDown(document.querySelector('[data-box="0"]') as Element, { clientX: 30, clientY: 90, pointerId: 1 });
    expect(screen.getByText(/characters per line/)).toBeTruthy();
    expect(screen.getByText(/1 of 5 words/)).toBeTruthy();
  });

  it("gives the stage the shape of the poster type", () => {
    render(<Harness initial={[]} onSpy={() => undefined} />);
    expect((screen.getByRole("application") as HTMLElement).style.aspectRatio).toBe("1");
  });
});
