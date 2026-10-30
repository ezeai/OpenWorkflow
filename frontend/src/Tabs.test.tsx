import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Tabs, type TabItem } from "./Tabs";

type View = "build" | "analysis" | "history";
const items: TabItem<View>[] = [
  { id: "build", label: "Build and run", panelId: "panel" },
  { id: "analysis", label: "Analysis", panelId: "panel" },
  { id: "history", label: "History", panelId: "panel" },
];

function setup(value: View = "build") {
  const onChange = vi.fn();
  render(<Tabs items={items} value={value} onChange={onChange} label="Workspace views" />);
  return { onChange, tabs: screen.getAllByRole("tab") };
}

afterEach(cleanup);

describe("Tabs", () => {
  it("marks only the selected tab as selected and as the Tab stop", () => {
    const { tabs } = setup("analysis");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["false", "true", "false"]);
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0, -1]);
  });

  it("is labelled as a tab list", () => {
    setup();
    expect(screen.getByRole("tablist", { name: "Workspace views" })).toBeTruthy();
  });

  it("selects the tab that is clicked", () => {
    const { onChange, tabs } = setup("build");
    fireEvent.click(tabs[2]);
    expect(onChange).toHaveBeenCalledWith("history");
  });

  it("ArrowRight moves to the next tab, moves focus with it, and wraps at the end", () => {
    const { onChange, tabs } = setup("build");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("analysis");
    expect(document.activeElement).toBe(tabs[1]);

    fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("build");
    expect(document.activeElement).toBe(tabs[0]);
  });

  it("ArrowLeft wraps from the first tab to the last", () => {
    const { onChange, tabs } = setup("build");
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("history");
    expect(document.activeElement).toBe(tabs[2]);
  });

  it("Home and End jump to the ends", () => {
    const { onChange, tabs } = setup("analysis");
    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("history");
    fireEvent.keyDown(tabs[1], { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("build");
  });

  it("ignores other keys", () => {
    const { onChange, tabs } = setup("build");
    fireEvent.keyDown(tabs[0], { key: "a" });
    expect(onChange).not.toHaveBeenCalled();
  });
});
