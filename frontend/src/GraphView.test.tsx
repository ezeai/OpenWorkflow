import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GraphView, depths, focusView, layout, slotCounts, traceOf, trail, type Graph } from "./GraphView";

const node = (id: string, type: string, pos: [number, number] | null = null) =>
  ({ id, type, title: type, state: "active", ui_only: false, group: null, params: [], pos, size: null });

const chain: Graph = {
  format: "ui",
  nodes: [node("1", "LoadImage"), node("2", "Resize"), node("3", "SaveImage")],
  links: [
    { id: "a", from: "1", from_slot: 0, to: "2", to_slot: 0, type: "IMAGE" },
    { id: "b", from: "2", from_slot: 0, to: "3", to_slot: 0, type: "IMAGE" },
  ],
};

afterEach(cleanup);

describe("graph layout", () => {
  it("puts each node after the nodes that feed it", () => {
    const d = depths(chain);
    expect([d.get("1"), d.get("2"), d.get("3")]).toEqual([0, 1, 2]);
  });

  it("does not loop on a cycle", () => {
    const cyc: Graph = { ...chain, links: [...chain.links, { id: "c", from: "3", from_slot: 0, to: "1", to_slot: 0, type: "IMAGE" }] };
    expect(() => depths(cyc)).not.toThrow();
  });

  it("uses the editor's own positions when every node has one", () => {
    const g: Graph = { ...chain, nodes: [node("1", "A", [100, 50]), node("2", "B", [400, 60]), node("3", "C", [700, 40])] };
    const { boxes } = layout(g);
    expect(boxes.get("1")?.x).toBe(100);
    expect(boxes.get("2")?.y).toBe(60);
  });

  it("counts slots from the links that use them", () => {
    const { ins, outs } = slotCounts(chain);
    expect(ins.get("3")).toBe(1);
    expect(outs.get("1")).toBe(1);
  });
});

describe("data-flow trace", () => {
  const flow: Graph = {
    format: "ui",
    nodes: [node("1", "Load"), node("2", "Resize"), node("3", "Sample"), node("4", "Save")],
    links: [
      { id: "a", from: "1", from_slot: 0, to: "2", to_slot: 0, type: "IMAGE" },
      { id: "b", from: "2", from_slot: 0, to: "3", to_slot: 0, type: "IMAGE" },
      { id: "c", from: "3", from_slot: 0, to: "4", to_slot: 0, type: "IMAGE" },
    ],
  };

  it("finds everything that feeds a node and everything it feeds", () => {
    const t = traceOf(flow, "3");
    expect([...t.up].sort()).toEqual(["1", "2"]);
    expect([...t.down]).toEqual(["4"]);
  });

  it("from the output back, the trace reaches every input", () => {
    expect([...traceOf(flow, "4").up].sort()).toEqual(["1", "2", "3"]);
    expect(traceOf(flow, "1").up.size).toBe(0);
  });
});

describe("focus and breadcrumb", () => {
  const nested: Graph = {
    format: "ui",
    nodes: [
      { ...node("1", "A"), scope: null },
      { ...node("1:5", "B"), scope: "1" },
      { ...node("1:5:7", "C"), scope: "1:5" },
    ],
    links: [{ id: "x", from: "1:5", from_slot: 0, to: "1:5:7", to_slot: 0, type: "IMAGE" }],
    boxes: [
      { scope: "1", parent: null, name: "Outer", label: "Outer" },
      { scope: "1:5", parent: "1", name: "Inner", label: "Outer › Inner" },
    ],
  };

  it("shows only the nodes inside a box, nested ones included", () => {
    expect(focusView(nested, "1").nodes.map((n) => n.id)).toEqual(["1:5", "1:5:7"]);
    expect(focusView(nested, "1:5").nodes.map((n) => n.id)).toEqual(["1:5:7"]);
    expect(focusView(nested, null).nodes.length).toBe(3);
  });

  it("keeps only the links whose two ends are both shown", () => {
    expect(focusView(nested, "1").links.length).toBe(1);
    expect(focusView(nested, "1:5").links.length).toBe(0);
  });

  it("builds the breadcrumb from the outside in", () => {
    expect(trail(nested.boxes ?? [], "1:5").map((b) => b.name)).toEqual(["Outer", "Inner"]);
    expect(trail(nested.boxes ?? [], null)).toEqual([]);
  });
});

describe("GraphView", () => {
  it("loads the graph when opened and selects a node to show its connections", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(chain), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<GraphView workflowId="w" />);
    fireEvent.click(screen.getByRole("button", { name: "Show the graph" }));
    expect(await screen.findByRole("img", { name: /Node graph with 3 nodes and 2 links/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Resize, node 2/ }));
    await waitFor(() => expect(screen.getByText(/input 0 ← LoadImage/)).toBeTruthy());
    expect(screen.getByText(/output 0 → SaveImage/)).toBeTruthy();
    vi.unstubAllGlobals();
  });
});
