// @vitest-environment jsdom
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { host } from "../port/host";
import type { TreeWorkspace } from "../port/hub";
import { useUnreadBadge } from "./unread";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const book = (unread: boolean, archived = false): TreeWorkspace[] => [{ root: "/work", name: "work", sessions: [{ path: "/session", name: "task", unread, archived }] }];

it("includes remote unread completions and excludes archived sessions", () => {
  const badge = vi.spyOn(host(), "setUnreadBadge").mockResolvedValue();
  const { rerender } = renderHook(({ remote }) => useUnreadBadge(book(true, true), { server: remote }), { initialProps: { remote: book(true) } });
  expect(badge).toHaveBeenLastCalledWith(true);
  rerender({ remote: book(false) });
  expect(badge).toHaveBeenLastCalledWith(false);
});

it("does not reissue an unchanged badge or break the app when a host refuses", async () => {
  const badge = vi.spyOn(host(), "setUnreadBadge").mockRejectedValue(new Error("unavailable"));
  const { rerender } = renderHook(() => useUnreadBadge(book(true), {}));
  await Promise.resolve();
  rerender();
  expect(badge).toHaveBeenCalledExactlyOnceWith(true);
});
