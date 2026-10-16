// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { Workspaces } from "./Workspaces";
import type { TreeWorkspace } from "../port/hub";

afterEach(cleanup);

const SESSION = "/w/.reasonix/sessions/20260901-120000.jsonl";

function draw() {
  const workspaces: TreeWorkspace[] = [{ root: "/w", name: "w", sessions: [{ path: SESSION, name: "session", title: "first title" }] }];
  const onRename = vi.fn();
  const onOpen = vi.fn().mockResolvedValue(undefined);
  render(
    <Workspaces
      hub={{} as never}
      tree={workspaces}
      treeRead
      runtimes={[]}
      active=""
      folded={new Set()}
      onFold={() => {}}
      reload={async () => {}}
      onOpen={onOpen}
      onFocus={() => {}}
      onClose={async () => {}}
      liveIds={() => []}
      runs={{}}
      onRename={onRename}
      onError={() => {}}
      adder={{ add: () => {}, close: () => {}, at: null } as never}
    />,
  );
  return { onRename };
}

it("double-clicking a session row opens the rename field", async () => {
  draw();
  await userEvent.dblClick(screen.getByRole("treeitem", { name: /first title/ }));
  const field = screen.getByRole("textbox", { name: "重命名该会话" }) as HTMLInputElement;
  expect(field.value).toBe("first title");
});

it("commits the new name on Enter and abandons it on Escape", async () => {
  const { onRename } = draw();
  await userEvent.dblClick(screen.getByRole("treeitem", { name: /first title/ }));
  await userEvent.keyboard("{Escape}");
  expect(onRename).not.toHaveBeenCalled();
  expect(screen.queryByRole("textbox", { name: "重命名该会话" })).toBeNull();
  await userEvent.dblClick(screen.getByRole("treeitem", { name: /first title/ }));
  await userEvent.clear(screen.getByRole("textbox", { name: "重命名该会话" }));
  await userEvent.type(screen.getByRole("textbox", { name: "重命名该会话" }), "renamed{Enter}");
  expect(onRename).toHaveBeenCalledWith(SESSION, "renamed", "inline");
});

it("a double-click inside the open field does not restart the edit", async () => {
  draw();
  await userEvent.dblClick(screen.getByRole("treeitem", { name: /first title/ }));
  const field = screen.getByRole("textbox", { name: "重命名该会话" });
  await userEvent.dblClick(field);
  expect(screen.getAllByRole("textbox", { name: "重命名该会话" })).toHaveLength(1);
});
