// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import "./testkit";
import { Transcript } from "./Transcript";
import type { Item } from "../state/session";

const renders = vi.hoisted(() => new Map<string, number>());
vi.mock("./cards/SayCard", () => ({
  SayCard: ({ item }: { item: { id: string } }) => {
    renders.set(item.id, (renders.get(item.id) ?? 0) + 1);
    return null;
  },
}));

afterEach(() => {
  cleanup();
  renders.clear();
});

const noop = async () => undefined as never;
const same = { entering: [] as string[], onEntered: () => {}, waiting: {}, scroll: { current: null }, onPinned: () => {}, onExtInvoke: () => {}, checkpoints: new Map(), onOpenProject: () => {}, onKeepHere: () => {}, takeovers: {} };
const view = (items: Item[]) => (
  <Transcript items={items} {...same} revision={1} hidden={false} jump={0} focus={null} onApprove={noop} onFullAccess={noop} onPlan={noop} onAnswer={noop} onForget={noop}
    onExtSubmit={noop} onPrepareRewind={noop} onCommitRewind={noop} onPrepareFileRevert={noop} onCommitFileRevert={noop} needsProject={false} />
);

describe("streaming into a transcript", () => {
  it("leaves the rows that did not change alone, however many chunks arrive", () => {
    const early: Item[] = [
      { t: "user", id: "u1", text: "a", authoredTurn: 1, msgIndex: 0 },
      { t: "say", id: "s1", text: "first", done: true },
      { t: "say", id: "s2", text: "second", done: true },
    ];
    const tail = (text: string): Item => ({ t: "say", id: "s3", text, done: false });
    const { rerender } = render(view([...early, tail("x")]));
    const settled = new Map(renders);
    for (let i = 0; i < 20; i++) rerender(view([...early, tail("x".repeat(i + 2))]));
    for (const id of ["s1", "s2"]) expect(renders.get(id), id).toBe(settled.get(id));
    expect(renders.get("s3")).toBeGreaterThan(settled.get("s3")!);
  });
});
