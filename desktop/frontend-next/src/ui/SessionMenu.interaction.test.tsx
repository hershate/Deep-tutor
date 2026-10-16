// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { SessionMenu } from "./SessionMenu";
import type { TreeSession } from "../port/hub";

afterEach(cleanup);

const session = (over: Partial<TreeSession> = {}): TreeSession => ({ path: "/s/a.jsonl", name: "a", title: "第一个", turns: 1, ...over });

function draw(s: TreeSession, extra: { live?: boolean; pinned?: boolean; exporting?: boolean } = {}) {
  const on = { onPin: vi.fn(), onRename: vi.fn(), onPause: vi.fn(), onClose: vi.fn(), onArchive: vi.fn(), onCopyInfo: vi.fn(), onExport: vi.fn(), onDelete: vi.fn() };
  render(<SessionMenu session={s} label="第一个" live={extra.live ?? false} pinned={extra.pinned ?? false} exporting={extra.exporting ?? false} {...on} />);
  return on;
}

it("offers the actions for an idle open conversation and reports which was chosen", async () => {
  const on = draw(session({ runtimeId: "r1" }));
  expect(screen.getByRole("menu", { name: "会话操作" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: /暂停执行/ })).toBeNull();
  await userEvent.click(screen.getByRole("menuitem", { name: /置顶会话/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: /重命名/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: /关闭会话/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: /归档会话/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: /删除会话/ }));
  expect([on.onPin, on.onRename, on.onClose, on.onArchive, on.onDelete].map((f) => f.mock.calls.length)).toEqual([1, 1, 1, 1, 1]);
});

it("a running conversation can be paused but not closed or archived", () => {
  draw(session({ runtimeId: "r1" }), { live: true });
  expect(screen.getByRole("menuitem", { name: /暂停执行/ })).toBeTruthy();
  expect((screen.getByRole("menuitem", { name: /关闭会话/ }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("menuitem", { name: /归档会话/ }) as HTMLButtonElement).disabled).toBe(true);
});

it("names the toggles by the state they will leave behind", () => {
  draw(session({ archived: true }), { pinned: true });
  expect(screen.getByRole("menuitem", { name: /取消置顶/ })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: /取消归档/ })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: /关闭会话/ })).toBeNull();
});

it("disables export while one is in flight", () => {
  draw(session(), { exporting: true });
  expect((screen.getByRole("menuitem", { name: /导出会话/ }) as HTMLButtonElement).disabled).toBe(true);
});
