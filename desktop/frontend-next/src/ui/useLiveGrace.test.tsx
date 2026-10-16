// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { setLiveGraceMinutes } from "../state/prefs";
import { useLiveGrace } from "./useLiveGrace";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const MIN = 60_000;

function setup() {
  const runsRef = { current: {} as Record<string, { run: string; live: boolean }> };
  const hook = renderHook(({ runs }) => useLiveGrace(runsRef, runs), { initialProps: { runs: runsRef.current } });
  // A pane report moves the run state after the hook has been told, as App does.
  const report = (id: string, live: boolean) => {
    act(() => {
      hook.result.current.noteReport(id, live);
      runsRef.current = { ...runsRef.current, [id]: { run: "", live } };
      hook.rerender({ runs: runsRef.current });
    });
  };
  return { hook, report };
}

it("drops a finished conversation at once while the grace is off", () => {
  const { hook, report } = setup();
  report("a", true);
  report("a", false);
  expect(hook.result.current.listedIds(["a"])).toEqual([]);
});

it("keeps a conversation seen finishing until its grace runs out, then redraws without it", () => {
  setLiveGraceMinutes(10);
  const { hook, report } = setup();
  report("a", true);
  expect(hook.result.current.listedIds(["a"])).toEqual(["a"]);
  report("a", false);
  const during = hook.result.current.listedIds;
  expect(during(["a"])).toEqual(["a"]);
  act(() => { vi.advanceTimersByTime(9 * MIN); });
  expect(hook.result.current.listedIds(["a"])).toEqual(["a"]);
  act(() => { vi.advanceTimersByTime(2 * MIN); });
  expect(hook.result.current.listedIds(["a"])).toEqual([]);
  // The expiry replaced the function, which is what re-draws a memoised list.
  expect(hook.result.current.listedIds).not.toBe(during);
});

it("lists a conversation again, with a fresh grace, when it runs once more", () => {
  setLiveGraceMinutes(10);
  const { hook, report } = setup();
  report("a", true);
  report("a", false);
  act(() => { vi.advanceTimersByTime(11 * MIN); });
  expect(hook.result.current.listedIds(["a"])).toEqual([]);
  report("a", true);
  expect(hook.result.current.listedIds(["a"])).toEqual(["a"]);
  report("a", false);
  expect(hook.result.current.listedIds(["a"])).toEqual(["a"]);
});

it("never lists a conversation that was idle from the start", () => {
  setLiveGraceMinutes(60);
  const { hook, report } = setup();
  report("b", false);
  expect(hook.result.current.listedIds(["b"])).toEqual([]);
});
