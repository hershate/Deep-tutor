// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "./testkit";
import { Pane } from "./Pane";
import { FoldWorkSetting } from "./FoldWorkSetting";
import { MockPort } from "../port/mock";
import { boot, STORAGE } from "../i18n";
import type { AgentPort } from "../port/port";
import type { RuntimeView } from "../port/hub";
import type { WireEvent } from "../port/wire";

Element.prototype.getAnimations ??= () => [];

afterEach(() => {
  cleanup();
  localStorage.setItem(STORAGE, "zh");
  boot();
  vi.restoreAllMocks();
});

const rt = { id: "p1", root: "/w", name: "w" } as RuntimeView;
const props = {
  rt, title: "w", active: true, visible: true, sideHost: null, side: false,
  onFocus() {}, onReport() {}, onSessionChanged() {}, pulse: 0, findPulse: 0,
  onSettings() {}, needsProject: false, onOpenProject() {}, onKeepHere() {},
  theme: "dark", dockW: 560, dockMax: 880, onDockW() {},
};

async function open(port = new MockPort()) {
  let emit: (ev: WireEvent) => void = () => {};
  const subscribe = port.subscribe.bind(port);
  vi.spyOn(port, "subscribe").mockImplementation((onEvent, onGap, bootstrap) => {
    emit = onEvent;
    return subscribe(onEvent, onGap, bootstrap);
  });
  let running = false;
  const readStatus = port.status.bind(port);
  vi.spyOn(port, "status").mockImplementation(async () => ({ ...(await readStatus()), running }));
  render(<Pane {...props} port={port as AgentPort} />);
  await act(async () => {});
  const send = (ev: object) => act(() => {
    const kind = (ev as { kind?: string }).kind;
    if (kind === "turn_started") running = true;
    if (kind === "turn_done") running = false;
    emit(ev as WireEvent);
  });
  return { port, send };
}

async function oneTurn(send: (ev: object) => unknown) {
  await send({ kind: "turn_started" });
  await send({ kind: "text", text: "let me look" });
  await send({ kind: "message", text: "let me look" });
  await send({ kind: "tool_dispatch", tool: { id: "t1", name: "bash", args: "{}" } });
  await send({ kind: "tool_result", tool: { id: "t1", name: "bash", args: "{}", output: "ok", durationMs: 5 } });
  await send({ kind: "text", text: "all done" });
  await send({ kind: "message", text: "all done" });
}

const folds = () => document.querySelectorAll("details.work-fold");

describe("folding a turn's work, as a kernel setting", () => {
  it("is off by default: a finished turn shows every step", async () => {
    const { send } = await open();
    await oneTurn(send);
    await send({ kind: "turn_done" });
    expect(folds()).toHaveLength(0);
  });

  it("reads the kernel's answer when a pane mounts, and folds a turn once it is done, not before", async () => {
    const port = new MockPort();
    await port.saveFoldWorkSettings(true);
    const { send } = await open(port);
    await oneTurn(send);
    expect(folds()).toHaveLength(0);
    await send({ kind: "turn_done" });
    await waitFor(() => expect(folds()).toHaveLength(1));
  });

  it("folds at once when switched on in a mounted pane, and unfolds when switched off", async () => {
    const { port, send } = await open();
    await oneTurn(send);
    await send({ kind: "turn_done" });
    const settings = render(<FoldWorkSetting port={port as AgentPort} />);
    const sw = settings.container.querySelector<HTMLButtonElement>('[data-action="chrome.fold-work"]')!;
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    await waitFor(() => expect(folds()).toHaveLength(1));
    fireEvent.click(sw);
    await waitFor(() => expect(folds()).toHaveLength(0));
    expect((await port.foldWorkSettings()).fold).toBe(false);
  });

  it("puts the switch back and says why when the kernel refuses the save", async () => {
    const port = new MockPort();
    vi.spyOn(port, "saveFoldWorkSettings").mockRejectedValue(new Error("disk full"));
    const settings = render(<FoldWorkSetting port={port as AgentPort} />);
    const sw = settings.container.querySelector<HTMLButtonElement>('[data-action="chrome.fold-work"]')!;
    fireEvent.click(sw);
    expect(await screen.findByText(/disk full/)).toBeTruthy();
    expect(sw.getAttribute("aria-checked")).toBe("false");
  });
});
