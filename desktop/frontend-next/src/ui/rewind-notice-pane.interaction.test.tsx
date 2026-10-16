// @vitest-environment jsdom
import "./testkit";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Pane } from "./Pane";
import { MockPort } from "../port/mock";
import { HttpError, type HistoryMessage, type RewindScope, type RewindUndo } from "../port/port";
import type { WireEvent } from "../port/wire";
import type { RuntimeView } from "../port/hub";

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

const PROMPT = "Create a sample file";
const HISTORY: HistoryMessage[] = [
  { role: "user", content: PROMPT, msgIndex: 0 },
  { role: "assistant", content: "Created the file.", msgIndex: 1 },
];
const props = {
  rt: { id: "p1", root: "/sample", name: "Sample" } as RuntimeView,
  title: "Sample", active: true, visible: true, sideHost: null, side: false,
  onFocus() {}, onReport() {}, onSessionChanged() {}, pulse: 0, findPulse: 0,
  onSettings() {}, needsProject: false, onOpenProject() {}, onKeepHere() {},
  theme: "dark", dockW: 560, dockMax: 880, onDockW() {},
};

function open(recovered = false) {
  const port = new MockPort();
  const listeners = new Set<(event: WireEvent) => void>();
  vi.spyOn(port, "subscribe").mockImplementation((listener) => {
    listeners.add(listener); return () => listeners.delete(listener);
  });
  let history = recovered ? [] : HISTORY;
  let offer: RewindUndo | null = recovered ? { transactionId: "tx-saved", turn: 1, files: 0 } : null;
  vi.spyOn(port, "history").mockImplementation(async () => history);
  vi.spyOn(port, "checkpoints").mockImplementation(async () => history.length ? [{
    turn: 1, prompt: PROMPT, files: 1, msgIndex: 0,
  }] : []);
  vi.spyOn(port, "prepareRewind").mockImplementation(async (turn, scope) => ({
    planId: scope, turn, coverage: "full", canFiles: true, canConversation: true,
    fileCount: 1, requiresConfirmation: false,
  }));
  vi.spyOn(port, "commitRewind").mockImplementation(async (scope) => {
    if (scope !== "code") history = [];
    offer = { transactionId: "tx-new", turn: 1, files: scope === "conversation" ? 0 : 1 };
    return { ok: true, conversationOk: scope !== "code", transactionId: offer.transactionId, undoAvailable: true };
  });
  vi.spyOn(port, "availableUndo").mockImplementation(async () => offer);
  vi.spyOn(port, "undoRewind").mockImplementation(async () => { history = HISTORY; offer = null; });
  return { port, mount: () => render(<Pane {...props} port={port} />), changeOffer(next: RewindUndo | null) {
    offer = next; listeners.forEach((listener) => listener({ kind: "rewind_changed" }));
  } };
}

it.each([
  ["code", "只还原代码"], ["conversation", "只回退对话"], ["both", "代码和对话"],
] as [RewindScope, string][])("offers undo after a %s rewind with realistic history", async (scope, label) => {
  const { port, mount } = open();
  mount();
  fireEvent.click((await screen.findAllByRole("button", { name: "回到这里" })).at(-1)!);
  fireEvent.click(screen.getByRole("menuitem", { name: new RegExp(label) }));
  const undo = await screen.findByRole("button", { name: "撤销这次回退" }, { timeout: 5000 });
  expect(port.prepareRewind).toHaveBeenCalledWith(1, scope);
  if (scope === "code") expect(screen.getByText(PROMPT)).toBeTruthy();
  else await waitFor(() => expect(screen.queryByRole("button", { name: "回到这里" })).toBeNull());
  fireEvent.click(undo);
  await waitFor(() => expect(port.undoRewind).toHaveBeenCalledExactlyOnceWith("tx-new"));
  await screen.findByText(PROMPT, { selector: ".txt" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "撤销这次回退" })).toBeNull());
});

it("recovers a conversation undo on a fresh pane without the removed user row", async () => {
  const { port, mount } = open(true);
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "撤销这次回退" }));
  await waitFor(() => expect(port.undoRewind).toHaveBeenCalledExactlyOnceWith("tx-saved"));
  await screen.findByText(PROMPT, { selector: ".txt" });
});

it("leaves the normal rewind menu usable when the host lacks the availability route", async () => {
  const { port, mount } = open();
  vi.mocked(port.availableUndo).mockRejectedValue(new HttpError(404, "not found"));
  mount();
  fireEvent.click((await screen.findAllByRole("button", { name: "回到这里" })).at(-1)!);
  expect(screen.getByRole("menuitem", { name: /只还原代码/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "撤销这次回退" })).toBeNull();
});

it("refreshes an idle pane from a content-free event without a local turn", async () => {
  const { mount, changeOffer } = open();
  mount();
  await screen.findAllByRole("button", { name: "回到这里" });
  changeOffer({ transactionId: "remote-tx", turn: 1, files: 0 });
  await screen.findByRole("button", { name: "撤销这次回退" });
  changeOffer(null);
  await waitFor(() => expect(screen.queryByRole("button", { name: "撤销这次回退" })).toBeNull());
});

it("keeps a recovered offer dismissed when the entire pane is reopened", async () => {
  const { port, mount } = open(true);
  const view = mount();
  fireEvent.click(await screen.findByRole("button", { name: "关闭回退提示" }));
  expect(port.undoRewind).not.toHaveBeenCalled();
  view.unmount();
  mount();
  await act(async () => {});
  expect(screen.queryByRole("button", { name: "撤销这次回退" })).toBeNull();
});

it("shows a recovered undo failure and allows retry through the session owner", async () => {
  const { port, mount } = open(true);
  vi.mocked(port.undoRewind).mockRejectedValueOnce(new Error("files changed"));
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "撤销这次回退" }));
  expect((await screen.findByRole("alert")).textContent).toContain("files changed");
  await waitFor(() => expect(screen.getByRole("button", { name: "撤销这次回退" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "撤销这次回退" }));
  await screen.findByText(PROMPT, { selector: ".txt" });
  expect(port.undoRewind).toHaveBeenCalledTimes(2);
});
