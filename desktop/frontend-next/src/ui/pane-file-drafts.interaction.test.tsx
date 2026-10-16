// @vitest-environment jsdom
import "./testkit";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MockPort } from "../port/mock";
import type { SessionStatus } from "../port/port";
import { Pane } from "./Pane";

afterEach(cleanup);
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;

it("keeps a file draft when the initial status confirms the runtime's known workspace", async () => {
  const port = new MockPort();
  const snapshot = { ...await port.status(), workspaceRoot: "/workspace" };
  let release!: (status: SessionStatus) => void;
  vi.spyOn(port, "status").mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  vi.spyOn(port, "subscribe").mockImplementation(() => () => true);
  vi.spyOn(port, "workspaceFiles").mockResolvedValue({ files: ["a.txt"], directories: [] });
  vi.spyOn(port, "workspaceFile").mockResolvedValue({ path: "a.txt", content: "saved", revision: "r1" });
  const save = vi.spyOn(port, "saveWorkspaceFile");
  const report = vi.fn();
  const user = userEvent.setup();
  render(<Pane port={port} rt={{ id: "r1", base: "/rt/r1", root: "/workspace", name: "workspace" }}
    title="active" active visible sideHost={null} side={false} onFocus={() => {}} onReport={report}
    onSessionChanged={() => {}} pulse={0} findPulse={0} onSettings={() => {}} needsProject={false}
    onOpenProject={() => {}} onKeepHere={() => {}} theme="light" dockW={560} dockMax={880} onDockW={() => {}} manualBrowser />);
  await user.click(await screen.findByRole("button", { name: "a.txt" }));
  const view = await waitFor(() => {
    const element = document.querySelector(".cm-content");
    if (!element) throw new Error("editor not mounted");
    return EditorView.findFromDOM(element as HTMLElement)!;
  });
  act(() => view.dispatch({ changes: { from: view.state.doc.length, insert: " unsaved" } }));
  await act(async () => release(snapshot));
  await waitFor(() => expect(report.mock.lastCall?.[1].status?.workspaceRoot).toBe("/workspace"));
  expect(document.querySelector(".cm-content")?.textContent).toBe("saved unsaved");
  expect(save).not.toHaveBeenCalled();
});
