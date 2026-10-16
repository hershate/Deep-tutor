// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { MockPort } from "../port/mock";
import { WorkbenchPanel } from "./WorkbenchPanel";
import { Composer } from "./Composer";
import "./testkit";

const props = (port: MockPort) => ({
  port, workspaceRoot: "/work/project", tabs: [], manual: false, shown: true, scheme: "light" as const,
  changes: [], onCloseManual: vi.fn(), onSurfaces: vi.fn(), onExternal: vi.fn(),
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Both({ port, onSubmit }: { port: MockPort; onSubmit: (text: string) => Promise<boolean> }) {
  const [inject, setInject] = useState({ n: 0, text: "" });
  return (
    <>
      <WorkbenchPanel {...props(port)} onAddToChat={(reference) => setInject((was) => ({ n: was.n + 1, text: reference }))} />
      <Composer port={port} status={null} running={false} inject={inject} onSubmit={onSubmit} onChanged={vi.fn()} onError={vi.fn()} />
    </>
  );
}

async function openMenuOn(path: string) {
  fireEvent.contextMenu(await screen.findByRole("button", { name: (name) => name.trim() === path.split("/").at(-1) }));
  return screen.getByRole("menuitem", { name: "加入对话" }) as HTMLButtonElement;
}

it.each([
  ["src/two words.ts", false, "@src/two\\ words.ts"],
  ["docs", true, "@docs/"],
])("adds %j to the conversation as the same reference Copy reference gives", async (path, folder, reference) => {
  const user = userEvent.setup();
  const port = new MockPort();
  vi.spyOn(port, "workspaceFiles").mockResolvedValue({ files: folder ? [] : [path], directories: folder ? [path] : [] });
  const submit = vi.spyOn(port, "submit");
  const onSubmit = vi.fn().mockResolvedValue(true);
  render(<Both port={port} onSubmit={onSubmit} />);
  const box = screen.getByRole("combobox", { name: "任务输入" }) as HTMLTextAreaElement;
  await user.click(box);
  await user.keyboard("look at ");
  await user.click(await openMenuOn(path));
  await waitFor(() => expect(box.value).toBe(`look at ${reference} `));
  expect(document.activeElement).toBe(box);
  expect(submit).not.toHaveBeenCalled();
  await user.keyboard("please{Enter}");
  await waitFor(() => expect(onSubmit.mock.calls[0]?.[0]).toBe(`look at ${reference} please`));
});

it("puts a space before the reference when the caret follows a word, and keeps what follows", async () => {
  const user = userEvent.setup();
  const port = new MockPort();
  vi.spyOn(port, "workspaceFiles").mockResolvedValue({ files: ["a.ts"], directories: [] });
  render(<Both port={port} onSubmit={vi.fn().mockResolvedValue(true)} />);
  const box = screen.getByRole("combobox", { name: "任务输入" }) as HTMLTextAreaElement;
  await user.click(box);
  await user.keyboard("compare with this");
  await act(async () => { box.setSelectionRange(7, 7); fireEvent.click(box); });
  await user.click(await openMenuOn("a.ts"));
  await waitFor(() => expect(box.value).toBe("compare @a.ts with this"));
});

it("offers nothing to add for a name that cannot be written as a reference", async () => {
  const port = new MockPort();
  vi.spyOn(port, "workspaceFiles").mockResolvedValue({ files: ["github:notes.md"], directories: [] });
  render(<Both port={port} onSubmit={vi.fn().mockResolvedValue(true)} />);
  const item = await openMenuOn("github:notes.md");
  expect(item.disabled).toBe(true);
  expect(item.title).toBe("此名称可能被识别为 MCP 资源");
});
