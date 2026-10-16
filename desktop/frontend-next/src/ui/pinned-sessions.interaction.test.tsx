// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import "./testkit";
import { Workspaces } from "./Workspaces";
import type { TreeWorkspace } from "../port/hub";
import { RailSearch } from "./railsearch";

afterEach(cleanup);

const tree = (): TreeWorkspace[] => [{
  root: "/w", name: "Project",
  sessions: [{ path: "/a.jsonl", name: "a", title: "Alpha" }, { path: "/b.jsonl", name: "b", title: "Beta" }, { path: "/c.jsonl", name: "c", title: "Gamma" }],
} as TreeWorkspace];

const order = () => screen.getAllByRole("treeitem").map((row) => within(row).queryByText(/Alpha|Beta|Gamma/)?.textContent).filter(Boolean);

function draw(pinned: string[]) {
  const props = { hub: {} as never, tree: tree(), treeRead: true, runtimes: [] as never, active: "", folded: new Set<string>(), onFold: vi.fn(),
    reload: vi.fn().mockResolvedValue(undefined), onOpen: vi.fn(), onFocus: vi.fn(), onClose: vi.fn().mockResolvedValue(undefined),
    liveIds: () => [] as string[], runs: {}, pinned: new Set(pinned), onArchive: vi.fn(), onRename: vi.fn(), onError: vi.fn(),
    adder: { add: vi.fn(), close: vi.fn(), at: null } as never };
  render(<RailSearch><Workspaces {...props} /></RailSearch>);
}

it("lists a pinned conversation first in its project and marks it", () => {
  draw(["/c.jsonl"]);
  expect(order()).toEqual(["Gamma", "Alpha", "Beta"]);
  const gamma = screen.getAllByRole("treeitem").find((row) => within(row).queryByText("Gamma"))!;
  expect(within(gamma).getByLabelText("已置顶")).toBeTruthy();
  expect(screen.getAllByLabelText("已置顶")).toHaveLength(1);
});

it("keeps the kernel's order within the pinned group and within the rest", () => {
  draw(["/b.jsonl", "/c.jsonl"]);
  expect(order()).toEqual(["Beta", "Gamma", "Alpha"]);
});

it("changes nothing when nothing is pinned", () => {
  draw([]);
  expect(order()).toEqual(["Alpha", "Beta", "Gamma"]);
  expect(screen.queryByLabelText("已置顶")).toBeNull();
});
