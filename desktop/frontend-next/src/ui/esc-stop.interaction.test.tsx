// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { App } from "./App";
import { MockHub } from "../port/mock_hub";
import { setEscStop } from "../state/escstop";
import { STOP_ARM_MS } from "./windowkeys";

beforeEach(() => {
  localStorage.clear();
  setEscStop("confirm");
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

async function openRunningWindow() {
  const hub = new MockHub();
  const [rt] = await hub.runtimes();
  const port = hub.portFor(rt);
  port.providerSetup = async () => null;
  port.welcomeSeen = async () => true;
  const status = await port.status();
  vi.spyOn(port, "status").mockResolvedValue({ ...status, running: true });
  const cancel = vi.spyOn(port, "cancel").mockResolvedValue(undefined);
  await act(async () => { render(<App hub={hub} />); });
  await waitFor(() => expect(document.querySelector(".studio-runstate[data-running]")).not.toBeNull());
  return { cancel };
}

const hint = () => document.querySelector<HTMLElement>(".stophint")!;

describe("Escape on a running turn", () => {
  it("asks for a second press before it stops", async () => {
    const { cancel } = await openRunningWindow();
    expect(hint().hidden).toBe(true);
    await userEvent.keyboard("{Escape}");
    expect(cancel).not.toHaveBeenCalled();
    expect(hint().hidden).toBe(false);
    expect(hint().textContent).toBe("再按一次 Esc 停止当前任务");
    await userEvent.keyboard("{Escape}");
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(hint().hidden).toBe(true);
  });

  it("forgets the first press after a moment, so a late second one only asks again", async () => {
    const { cancel } = await openRunningWindow();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await userEvent.keyboard("{Escape}");
    expect(hint().hidden).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(STOP_ARM_MS + 50); });
    expect(hint().hidden).toBe(true);
    await userEvent.keyboard("{Escape}");
    expect(cancel).not.toHaveBeenCalled();
    expect(hint().hidden).toBe(false);
  });

  it("stops at once when the setting says so", async () => {
    setEscStop("now");
    const { cancel } = await openRunningWindow();
    await userEvent.keyboard("{Escape}");
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(hint().hidden).toBe(true);
  });

  it("is a choice in Appearance that takes effect without a restart", async () => {
    const { cancel } = await openRunningWindow();
    expect(screen.queryByRole("radio", { name: "按一次 Esc 立即停止" })).toBeNull();
    act(() => setEscStop("now"));
    await userEvent.keyboard("{Escape}");
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
