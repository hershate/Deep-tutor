// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { RewindNotice } from "./RewindNotice";
import { MockPort } from "../port/mock";
import type { RewindUndo } from "../port/port";

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); vi.useRealTimers(); });

const saved: RewindUndo = { transactionId: "tx-saved", turn: 2, files: 0 };
function draw() {
  let available: RewindUndo | null = saved;
  const readUndo = vi.fn(async () => available);
  const onUndo = vi.fn(async (_id: string) => { available = null; });
  const props = { readUndo, onUndo, refreshKey: 0, running: false, shown: true };
  const view = render(<RewindNotice {...props} />);
  return { props, view, readUndo, onUndo, invalidate: () => { available = null; } };
}

it("recovers a saved offer on a fresh mount without any message card", async () => {
  const { props, view } = draw();
  await screen.findByRole("button", { name: "撤销这次回退" });
  view.unmount();
  render(<RewindNotice {...props} />);
  await screen.findByRole("button", { name: "撤销这次回退" });
  expect(screen.getByRole("status").textContent).toContain("回退已完成");
});

it("hides the notice when undo succeeds", async () => {
  const { onUndo } = draw();
  await userEvent.click(await screen.findByRole("button", { name: "撤销这次回退" }));
  expect(onUndo).toHaveBeenCalledWith("tx-saved");
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
});

it("shows a failed undo without losing the backend offer", async () => {
  const { onUndo } = draw();
  onUndo.mockRejectedValueOnce(new Error("file changed"));
  await userEvent.click(await screen.findByRole("button", { name: "撤销这次回退" }));
  expect((await screen.findByRole("alert")).textContent).toContain("file changed");
  expect(screen.getByRole("button", { name: "撤销这次回退" }).hasAttribute("disabled")).toBe(false);
});

it("hides during a new turn and does not revive an invalidated offer afterwards", async () => {
  const { props, view, invalidate, readUndo } = draw();
  await screen.findByRole("button", { name: "撤销这次回退" });
  view.rerender(<RewindNotice {...props} running />);
  expect(screen.queryByRole("status")).toBeNull();
  invalidate();
  view.rerender(<RewindNotice {...props} running={false} />);
  await waitFor(() => expect(readUndo).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("status")).toBeNull();
});

it("does not poll while a visible pane is idle", async () => {
  vi.useFakeTimers();
  const { readUndo } = draw();
  await act(async () => {});
  await act(async () => { vi.advanceTimersByTime(30000); });
  expect(readUndo).toHaveBeenCalledTimes(1);
});

it("reads once when an external undo-change event arrives", async () => {
  const { props, view, invalidate, readUndo } = draw();
  await screen.findByRole("button", { name: "撤销这次回退" });
  invalidate();
  view.rerender(<RewindNotice {...props} moved={1} />);
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  expect(readUndo).toHaveBeenCalledTimes(2);
});

it("ignores an old session's query after switching to another session", async () => {
  let finishOld!: (value: RewindUndo | null) => void;
  const old = new Promise<RewindUndo | null>((resolve) => { finishOld = resolve; });
  const next = { ...saved, transactionId: "tx-next" };
  const readUndo = vi.fn().mockReturnValueOnce(old).mockResolvedValue(next);
  const onUndo = vi.fn(async (_id: string) => { readUndo.mockResolvedValue(null); });
  const props = { readUndo, onUndo, refreshKey: 0, running: false, shown: true };
  const view = render(<RewindNotice key="old-session" {...props} />);
  view.rerender(<RewindNotice key="next-session" {...props} />);
  await screen.findByRole("button", { name: "撤销这次回退" });
  await act(async () => { finishOld(saved); });
  await userEvent.click(screen.getByRole("button", { name: "撤销这次回退" }));
  expect(onUndo).toHaveBeenCalledWith("tx-next");
});

it("keeps a dismissed offer hidden after an event refresh and remount", async () => {
  const { props, view, readUndo, onUndo } = draw();
  await userEvent.click(await screen.findByRole("button", { name: "关闭回退提示" }));
  expect(screen.queryByRole("status")).toBeNull();
  expect(onUndo).not.toHaveBeenCalled();
  view.rerender(<RewindNotice {...props} moved={1} />);
  await waitFor(() => expect(readUndo).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("status")).toBeNull();
  view.unmount();
  render(<RewindNotice {...props} />);
  await waitFor(() => expect(readUndo).toHaveBeenCalledTimes(3));
  expect(screen.queryByRole("status")).toBeNull();
});

it("shows a new transaction without reviving dismissed transactions", async () => {
  const { props, view, readUndo } = draw();
  await userEvent.click(await screen.findByRole("button", { name: "关闭回退提示" }));
  const next = { ...saved, transactionId: "tx-new" };
  readUndo.mockResolvedValue(next);
  view.rerender(<RewindNotice {...props} moved={1} />);
  expect(await screen.findByRole("button", { name: "撤销这次回退" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "关闭回退提示" }));
  readUndo.mockResolvedValue(saved);
  view.rerender(<RewindNotice {...props} moved={2} />);
  await waitFor(() => expect(readUndo).toHaveBeenCalledTimes(3));
  expect(screen.queryByRole("status")).toBeNull();
});

it("dismisses the same transaction in other mounted panes", async () => {
  const props = { readUndo: vi.fn(async () => saved), onUndo: vi.fn(), refreshKey: 0, running: false, shown: true };
  render(<><RewindNotice {...props} /><RewindNotice {...props} /></>);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "关闭回退提示" })).toHaveLength(2));
  await userEvent.click(screen.getAllByRole("button", { name: "关闭回退提示" })[0]!);
  expect(screen.queryAllByRole("status")).toHaveLength(0);
  expect(props.onUndo).not.toHaveBeenCalled();
});

it("reports a persistence failure rather than claiming the notice was dismissed", async () => {
  const { onUndo } = draw();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage blocked"); });
  await userEvent.click(await screen.findByRole("button", { name: "关闭回退提示" }));
  expect((await screen.findByRole("alert")).textContent).toContain("storage blocked");
  expect(screen.getByRole("button", { name: "撤销这次回退" })).toBeTruthy();
  expect(onUndo).not.toHaveBeenCalled();
});

it("gives repeated mock rewinds their own transaction identity", async () => {
  const port = new MockPort();
  const first = await port.commitRewind("mock-plan-0-code");
  const next = await port.commitRewind("mock-plan-0-code");
  expect(next.transactionId).not.toBe(first.transactionId);
});

it("observes dismissal persisted by another browser window", async () => {
  draw();
  await screen.findByRole("button", { name: "关闭回退提示" });
  await act(async () => {
    const key = "rx-rewind-notice-dismissed:" + saved.transactionId;
    localStorage.setItem(key, "1");
    window.dispatchEvent(new StorageEvent("storage", { key }));
  });
  expect(screen.queryByRole("status")).toBeNull();
});

it("disables actions while the session owner is completing a restore or refresh", async () => {
  const { props, view, onUndo } = draw();
  await screen.findByRole("button", { name: "撤销这次回退" });
  view.rerender(<RewindNotice {...props} busy />);
  expect(screen.getByRole("button", { name: "撤销这次回退" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "关闭回退提示" }).hasAttribute("disabled")).toBe(true);
  expect(onUndo).not.toHaveBeenCalled();
});
