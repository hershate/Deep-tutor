// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { MockPort } from "../port/mock";
import { HttpError, type AgentPort, type WorkspaceChange } from "../port/port";
import { WorkbenchChanges } from "./WorkbenchChanges";

afterEach(cleanup);

const changes: WorkspaceChange[] = [
  { path: "src/edited.go", status: "M", unstaged: true },
  { path: "src/ready.go", status: "M", staged: true },
  { path: "src/both.go", status: "A", staged: true, unstaged: true },
  { path: "notes.md", status: "??", unstaged: true },
];

function draw(list: WorkspaceChange[] = changes, port?: AgentPort) {
  const p = port ?? (new MockPort() as unknown as AgentPort);
  p.stageChanges = vi.fn(async () => {});
  const onChanged = vi.fn();
  const onOpen = vi.fn();
  render(
    <WorkbenchChanges port={p} changes={list} current="" onOpen={onOpen} onChanged={onChanged}
      commit={{ button: <button data-action="commit.draft">提交…</button>, card: null }} />,
  );
  return { port: p, onChanged, onOpen };
}

const box = (path: string) => screen.getByRole("checkbox", { name: `暂存 ${path}` });

describe("the changes list stages and unstages what a commit will record", () => {
  it("shows each file as staged, partly staged or not staged", () => {
    draw();
    expect(box("src/edited.go").getAttribute("aria-checked")).toBe("false");
    expect(box("src/ready.go").getAttribute("aria-checked")).toBe("true");
    expect(box("src/both.go").getAttribute("aria-checked")).toBe("mixed");
    expect(box("notes.md").getAttribute("aria-checked")).toBe("false");
  });

  it("stages one file by ticking it, and unstages a staged one, then asks for the list again", async () => {
    const { port, onChanged } = draw();
    await userEvent.click(box("notes.md"));
    expect(port.stageChanges).toHaveBeenLastCalledWith({ paths: ["notes.md"], unstage: false });
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    await userEvent.click(box("src/ready.go"));
    expect(port.stageChanges).toHaveBeenLastCalledWith({ paths: ["src/ready.go"], unstage: true });
  });

  it("finishes a partly staged file by staging it, rather than undoing what was staged", async () => {
    const { port } = draw();
    await userEvent.click(box("src/both.go"));
    expect(port.stageChanges).toHaveBeenLastCalledWith({ paths: ["src/both.go"], unstage: false });
  });

  it("stages everything at once while anything is unstaged, and unstages everything when nothing is left", async () => {
    const { port } = draw();
    await userEvent.click(screen.getByRole("button", { name: "暂存全部" }));
    expect(port.stageChanges).toHaveBeenLastCalledWith({ all: true, unstage: false });
    cleanup();
    const done = draw([{ path: "a.go", status: "M", staged: true }]);
    await userEvent.click(screen.getByRole("button", { name: "取消全部暂存" }));
    expect(done.port.stageChanges).toHaveBeenLastCalledWith({ all: true, unstage: true });
  });

  it("opening a file's diff still works from its name", async () => {
    const { onOpen } = draw();
    await userEvent.click(screen.getByRole("button", { name: /edited\.go/ }));
    expect(onOpen).toHaveBeenCalledWith("src/edited.go");
  });

  it("says why staging failed and keeps the list as it was", async () => {
    const port = new MockPort() as unknown as AgentPort;
    const { onChanged } = draw(changes, port);
    port.stageChanges = vi.fn(async () => { throw new HttpError(400, "x", { code: "stage.unknown_path" }); });
    await userEvent.click(box("notes.md"));
    expect((await screen.findByRole("alert")).textContent).toContain("不在改动列表");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("holds every control while a request is in flight, so two clicks are one", async () => {
    let release: () => void = () => {};
    const port = new MockPort() as unknown as AgentPort;
    draw(changes, port);
    port.stageChanges = vi.fn(() => new Promise<void>((r) => { release = r; }));
    await userEvent.click(box("notes.md"));
    expect(box("src/edited.go").hasAttribute("disabled")).toBe(true);
    expect((screen.getByRole("button", { name: "暂存全部" }) as HTMLButtonElement).disabled).toBe(true);
    release();
    await waitFor(() => expect(box("src/edited.go").hasAttribute("disabled")).toBe(false));
  });

  it("offers no staging where the list cannot be refreshed", () => {
    const port = new MockPort() as unknown as AgentPort;
    render(<WorkbenchChanges port={port} changes={changes} current="" onOpen={() => {}} commit={{ button: null, card: null }} />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "暂存全部" })).toBeNull();
  });
});
