// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import type { AgentPort, Checkpoint } from "../port/port";
import type { Item } from "../state/session";
import { SayCard } from "./cards/SayCard";
import { useReplyActions } from "./reply";

afterEach(cleanup);

const checkpoint = (turn: number, msgIndex: number): Checkpoint => ({ turn, msgIndex, prompt: "问", files: 0 });

const items: Item[] = [
  { t: "user", id: "u1", text: "第一问", msgIndex: 1 },
  { t: "say", id: "a1", text: "答一", done: true },
  { t: "user", id: "u2", text: "第二问", msgIndex: 5 },
  { t: "say", id: "a2", text: "答二", done: true },
];

function draw({ running = false, branchAt = vi.fn(async (_turn: number, _name: string) => "/s/branch.jsonl") } = {}) {
  const onBranched = vi.fn();
  const onError = vi.fn();
  const port = { branchAt } as unknown as AgentPort;
  function Replies() {
    const { reply } = useReplyActions({
      port, items, checkpoints: [checkpoint(0, 1), checkpoint(1, 5)], running, branchName: "我的会话 · 分支",
      submit: vi.fn(async () => true), reloadSession: vi.fn(async () => {}),
      onSettings: vi.fn(), onRunDetail: vi.fn(), onError, onBranched,
    });
    return <>{items.filter((i): i is Extract<Item, { t: "say" }> => i.t === "say").map((i) => <SayCard key={i.id} item={i} reply={reply} />)}</>;
  }
  render(<Replies />);
  return { branchAt, onBranched, onError };
}

it("branches a reply's conversation at its own turn and opens the result", async () => {
  const { branchAt, onBranched } = draw();
  const buttons = screen.getAllByRole("button", { name: "分支为新会话" });
  expect(buttons).toHaveLength(2);
  await userEvent.click(buttons[0]);
  await waitFor(() => expect(onBranched).toHaveBeenCalledWith("/s/branch.jsonl"));
  expect(branchAt).toHaveBeenCalledWith(0, "我的会话 · 分支");
  await userEvent.click(buttons[1]);
  await waitFor(() => expect(branchAt).toHaveBeenLastCalledWith(1, "我的会话 · 分支"));
});

it("is not offered while a turn runs", () => {
  draw({ running: true });
  expect(screen.queryByRole("button", { name: "分支为新会话" })).toBeNull();
});

it("reports a refusal instead of opening anything", async () => {
  const refusal = new Error("no boundary");
  const { onBranched, onError } = draw({ branchAt: vi.fn(async () => { throw refusal; }) });
  await userEvent.click(screen.getAllByRole("button", { name: "分支为新会话" })[0]);
  await waitFor(() => expect(onError).toHaveBeenCalledWith(refusal));
  expect(onBranched).not.toHaveBeenCalled();
});
