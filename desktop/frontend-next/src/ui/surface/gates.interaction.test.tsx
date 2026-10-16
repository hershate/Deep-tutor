// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, renderHook, act } from "@testing-library/react";
import { useGateActions } from "../gates";
import type { AgentPort } from "../../port/port";

afterEach(cleanup);
it("a typed answer seals only after acknowledgment and preserves a refused prompt", async () => {
  const dispatch = vi.fn();
  const fail = vi.fn();
  const port = { answer: vi.fn().mockRejectedValueOnce(new Error("refused")).mockResolvedValueOnce(undefined) } as unknown as AgentPort;
  const { result } = renderHook(() => useGateActions({ port, dispatch, fail, refreshStatus: vi.fn(), onRevise: vi.fn() }));
  const answers = [{ questionId: "surface", selected: [], value: { count: 2, enabled: false } }];
  await act(async () => { await expect(result.current.onAnswer("row-1", "ask-1", answers)).rejects.toThrow("refused"); });
  expect(dispatch).not.toHaveBeenCalled();
  expect(fail).toHaveBeenCalledTimes(1);
  await act(async () => { await result.current.onAnswer("row-1", "ask-1", answers); });
  expect(dispatch).toHaveBeenCalledWith({ kind: "__decided", id: "row-1", answers: [[]], values: { count: 2, enabled: false } });
});
