// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import "./testkit";
import * as i18n from "../i18n";
import { usePaint } from "./paint";
import type { HubPort } from "../port/hub";

const captured = vi.hoisted(() => ({ hub: null as HubPort | null }));
vi.mock("./App", () => ({ App: ({ hub }: { hub: HubPort }) => {
  captured.hub = hub;
  return null;
} }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("delivers the perf scene's language, zoom, theme, and save instrumentation to the window owner", async () => {
  localStorage.clear();
  history.replaceState({}, "", "?pref=zh&zoom=1.25&pack=sky&turns=2&git=none");
  document.body.innerHTML = '<div id="root"></div>';
  const adopt = vi.spyOn(i18n, "adopt").mockReturnValue(false);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  await act(async () => { await import("../../perf/harness"); });
  const hub = captured.hub!;
  const runtimes = await hub.runtimes();
  const onError = vi.fn();
  const { result } = renderHook(() => usePaint(hub, runtimes, false, onError));
  await waitFor(() => expect(result.current.look.language).toBe("zh"));
  expect(adopt).toHaveBeenCalledWith("zh");
  expect(result.current.look.language).toBe("zh");
  expect(result.current.look.zoom).toBe(1.25);
  expect(result.current.pack?.id).toBe("bench-sky");
  window.__saveLag(10);
  act(() => result.current.onLook({ ...result.current.look, readSize: 16 }));
  await waitFor(() => expect(window.__saves().count).toBe(1));
  expect(window.__saves().last?.readSize).toBe(16);
});
