// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import "./testkit";
import { MockHub } from "../port/mock_hub";
import type { TreeWorkspace } from "../port/hub";
import { Sidebar } from "./Sidebar";
import { NO_PAGING } from "./treewindow";

afterEach(cleanup);

const tree: TreeWorkspace[] = [{
  root: "/work/alpha", name: "alpha", remembered: true,
  sessions: [
    { path: "/work/alpha/a.jsonl", name: "a", title: "first" },
    { path: "/work/alpha/b.jsonl", name: "b", title: "second" },
  ],
}];

function draw() {
  const hub = new MockHub();
  const noop = vi.fn();
  render(
    <Sidebar
      onFiles={noop} hub={hub} collapsed={false} navShown paging={NO_PAGING} tree={tree} treeRead runtimes={[]} runs={{}} active=""
      activeWorkspace={undefined} liveIds={() => []} pinned={new Set()} onPin={noop}
      remotes={null} remoteTrees={{}} reloadRemotes={async () => {}} reloadRemoteTrees={async () => {}}
      readRemoteTree={async () => {}} reloadTree={async () => {}} adder={{ busy: false, add: noop, addPath: noop } as never}
      onOpen={async () => {}} onOpenRemote={async () => {}} onFocusPane={noop} onClosePanes={async () => {}}
      onPause={noop} onArchive={async () => {}} onRename={noop} onCollapse={noop} account={null} accountUnread="" wallet=""
      onSettings={noop} onFeedback={noop} onTutor={noop} feedbackUnread={0} onFind={noop} onError={noop}
    />,
  );
}

const scope = (value: string) => document.querySelector(`[data-action="session.filter"][data-value="${value}"]`)!;

it("shows a scope's count only when the scope holds something", () => {
  draw();
  expect(scope("all").querySelector("b")?.textContent).toBe("2");
  for (const empty of ["live", "pinned", "archived"]) expect(scope(empty).querySelector("b")).toBeNull();
});

it("tells the levels apart: a machine counts its projects in words, a project counts sessions in a pill", () => {
  draw();
  const machine = screen.getByRole("treeitem", { name: /这台机器/ });
  expect(within(machine).getByText("1 个项目")).toBeTruthy();
  const project = document.querySelector<HTMLElement>(".wsrow")!;
  const count = project.querySelector(".rowcount")!;
  expect(count.textContent).toBe("2");
  expect(count.getAttribute("data-pill")).not.toBeNull();
});
