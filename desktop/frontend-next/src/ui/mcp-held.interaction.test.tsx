// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { ServerRow } from "./ServerRow";
import type { AgentPort, McpEntry } from "../port/port";

afterEach(cleanup);

const withheld = (over: Partial<McpEntry> = {}): McpEntry => ({
  name: "notes", state: "ready", enabled: true, transport: "stdio", tools: 1,
  held: [{ name: "mcp__notes__search", class: "changed" }, { name: "mcp__notes__add", class: "added" }],
  heldDigest: "digest-1", ...over,
});

function draw(m: McpEntry, live = true) {
  const trustMcp = vi.fn(async () => ({ state: "ready", tools: 2 }));
  const onDone = vi.fn();
  render(<ServerRow m={m} port={{ trustMcp } as unknown as AgentPort} onDone={onDone} root="/w" live={live} />);
  return { trustMcp, onDone };
}

describe("tools a server changed since the user approved them", () => {
  it("lists each withheld tool with how it differs", () => {
    draw(withheld());
    const group = screen.getByRole("group", { name: "等待你批准的工具" });
    expect(group.textContent).toContain("mcp__notes__search");
    expect(group.textContent).toContain("定义有改动");
    expect(group.textContent).toContain("mcp__notes__add");
    expect(group.textContent).toContain("2 个工具");
  });

  it("opens a row that has tools, so the approval is not hidden behind a fold", () => {
    draw(withheld({ toolList: [{ name: "add", description: "Add a note" }] }));
    expect(document.querySelector("details.mcp-server")?.hasAttribute("open")).toBe(true);
  });

  it("approves exactly the digest it was shown, then refreshes the list", async () => {
    const { trustMcp, onDone } = draw(withheld());
    await userEvent.click(screen.getByRole("button", { name: "批准并重新连接" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(trustMcp).toHaveBeenCalledTimes(1);
    expect(trustMcp).toHaveBeenCalledWith("notes", "digest-1");
  });

  it("shows the kernel's refusal instead of claiming success when the set changed meanwhile", async () => {
    const trustMcp = vi.fn(async () => ({ state: "failed", error: "the held MCP tool definitions changed since they were shown" }));
    render(<ServerRow m={withheld()} port={{ trustMcp } as unknown as AgentPort} onDone={() => {}} root="/w" live />);
    await userEvent.click(screen.getByRole("button", { name: "批准并重新连接" }));
    expect(await screen.findByText(/changed since they were shown/)).toBeTruthy();
  });

  it("says how many more are withheld than listed", () => {
    draw(withheld({ heldMore: 7 }));
    expect(screen.getByRole("group", { name: "等待你批准的工具" }).textContent).toContain("9 个工具");
    expect(screen.getByText(/还有 7 个没有列出/)).toBeTruthy();
  });

  it("offers no approval from a row the person cannot edit, and nothing when none is withheld", () => {
    draw(withheld(), false);
    expect(screen.queryByRole("button", { name: "批准并重新连接" })).toBeNull();
    cleanup();
    draw(withheld({ held: undefined, heldDigest: undefined }));
    expect(screen.queryByRole("group", { name: "等待你批准的工具" })).toBeNull();
  });
});
