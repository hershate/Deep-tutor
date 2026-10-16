// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { watchTreeWake } from "./treewake";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
it("coalesces recovery bursts, bounds later reads, and cancels on detach", () => {
  vi.useFakeTimers();
  const refresh = vi.fn();
  const stop = watchTreeWake(refresh);
  window.dispatchEvent(new Event("focus"));
  window.dispatchEvent(new Event("online"));
  document.dispatchEvent(new Event("visibilitychange"));
  vi.advanceTimersByTime(100);
  expect(refresh).toHaveBeenCalledTimes(1);
  window.dispatchEvent(new Event("pageshow"));
  vi.advanceTimersByTime(500);
  window.dispatchEvent(new Event("focus"));
  vi.advanceTimersByTime(500);
  expect(refresh).toHaveBeenCalledTimes(2);
  window.dispatchEvent(new Event("online"));
  stop();
  vi.advanceTimersByTime(2000);
  window.dispatchEvent(new Event("focus"));
  vi.advanceTimersByTime(2000);
  expect(refresh).toHaveBeenCalledTimes(2);
});
it("waits until the page becomes visible", () => {
  vi.useFakeTimers();
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  const refresh = vi.fn();
  const stop = watchTreeWake(refresh);
  window.dispatchEvent(new Event("pageshow"));
  vi.advanceTimersByTime(1000);
  expect(refresh).not.toHaveBeenCalled();
  visible.mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  vi.advanceTimersByTime(100);
  expect(refresh).toHaveBeenCalledOnce();
  stop();
});
