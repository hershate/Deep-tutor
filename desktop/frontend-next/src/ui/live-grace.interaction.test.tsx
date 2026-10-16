// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { Workspaces } from "./Workspaces";
import { LiveGraceSection } from "./LiveGraceSection";
import { liveGraceMinutes } from "../state/prefs";
import type { TreeWorkspace } from "../port/hub";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

const PATH = "/w/.reasonix/sessions/session.jsonl";
const TITLE = "finished a moment ago";

function drawRunningList(listed: boolean) {
  const tree: TreeWorkspace[] = [{ root: "/w", name: "w", sessions: [{ path: PATH, name: "session", title: TITLE, runtimeId: "r1" }] }];
  render(<Workspaces
    hub={{ removeSession: vi.fn() } as never} tree={tree} treeRead
    runtimes={[{ id: "r1", base: "", root: "/w", name: "w", sessionPath: PATH }]}
    active="" folded={new Set()} onFold={() => {}} reload={vi.fn().mockResolvedValue(undefined)}
    onOpen={vi.fn()} onFocus={vi.fn()} onClose={vi.fn()}
    liveIds={() => []} listedIds={(ids) => (listed ? ids : [])} runs={{ r1: { run: "", live: false } }}
    scope="live"
    onArchive={vi.fn()} onRename={() => {}} onError={vi.fn()}
    adder={{ add: () => {}, close: () => {}, at: null } as never}
  />);
}

it("lists a session that finished within its grace in the running view", () => {
  drawRunningList(true);
  expect(screen.getByRole("treeitem", { name: new RegExp(TITLE) })).toBeTruthy();
});

it("leaves a session out of the running view once it is not listed", () => {
  drawRunningList(false);
  expect(screen.queryByRole("treeitem", { name: new RegExp(TITLE) })).toBeNull();
});

it("keeps a listed but finished session deletable: the grace is a list rule, not a run state", () => {
  drawRunningList(true);
  expect((screen.getByRole("button", { name: `删除会话：${TITLE}` }) as HTMLButtonElement).disabled).toBe(false);
});

it("offers the grace choices, starts off, and remembers a pick on this machine", async () => {
  render(<LiveGraceSection />);
  expect(screen.getByRole("button", { name: "不保留" }).getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "1 小时" }));
  expect(screen.getByRole("button", { name: "1 小时" }).getAttribute("aria-pressed")).toBe("true");
  expect(liveGraceMinutes()).toBe(60);
});
