// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import "./testkit";
import { Workspaces } from "./Workspaces";
import type { TreeWorkspace } from "../port/hub";
import { RailSearch } from "./railsearch";

afterEach(cleanup);

function draw(sessions: TreeWorkspace["sessions"]) {
  const props = { hub: {} as never, tree: [{ root: "/w", name: "w", sessions } as TreeWorkspace],
    treeRead: true, runtimes: [] as never, active: "", folded: new Set<string>(), onFold: vi.fn(),
    reload: vi.fn().mockResolvedValue(undefined), onOpen: vi.fn(), onFocus: vi.fn(), onClose: vi.fn().mockResolvedValue(undefined),
    liveIds: () => [] as string[], runs: {}, pinned: new Set<string>(), onArchive: vi.fn(), onRename: vi.fn(), onError: vi.fn(),
    adder: { add: vi.fn(), close: vi.fn(), at: null } as never };
  render(<RailSearch><Workspaces {...props} /></RailSearch>);
}

const mark = () => document.querySelector(".sesslegacy");

// A conversation 1.x kept is read here and never written, so the row says which
// store it came from rather than looking like one of this build's own.
it("marks a row whose log still lives in the 1.x store", () => {
  draw([{ path: "/w/.reasonix/sessions/old.jsonl", name: "old", title: "旧会话", legacy: true }]);
  expect(mark()?.textContent).toBeTruthy();
});

it("leaves this build's own conversation unmarked", () => {
  draw([{ path: "/w/.reasonix/sessions/mine.jsonl", name: "mine", title: "新会话" }]);
  expect(mark()).toBeNull();
});