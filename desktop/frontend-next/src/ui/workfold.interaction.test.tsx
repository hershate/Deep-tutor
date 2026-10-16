// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import "./testkit";
import { Transcript } from "./Transcript";
import { landingBox } from "./findland";
import type { Item } from "../state/session";

afterEach(cleanup);

const noop = async () => undefined as never;
const same = { entering: [] as string[], onEntered: () => {}, waiting: {}, scroll: { current: null }, onPinned: () => {}, onExtInvoke: () => {}, checkpoints: new Map(), onOpenProject: () => {}, onKeepHere: () => {}, takeovers: {} };
const view = (items: Item[], extra: { foldWork?: boolean; turnRunning?: boolean } = {}) => (
  <Transcript items={items} {...same} revision={items.length} hidden={false} jump={0} focus={null} onApprove={noop} onFullAccess={noop} onPlan={noop} onAnswer={noop} onForget={noop}
    onExtSubmit={noop} onPrepareRewind={noop} onCommitRewind={noop} onPrepareFileRevert={noop} onCommitFileRevert={noop} needsProject={false} {...extra} />
);
const tool = (id: string, err?: string): Item => ({ t: "tool", id, running: false, children: [], tool: { id, name: "bash", args: "{}", readOnly: false, ...(err ? { err } : {}) } as never });
const turn: Item[] = [
  { t: "user", id: "u1", text: "fix it", authoredTurn: 1, msgIndex: 0 },
  { t: "say", id: "s1", text: "looking", done: true },
  tool("t1"),
  { t: "say", id: "s2", text: "found the cause", done: true },
  tool("t2"),
  { t: "say", id: "s3", text: "all done", done: true },
];
const folds = () => [...document.querySelectorAll<HTMLDetailsElement>("details.work-fold")];
const inside = (id: string) => folds().some((f) => f.querySelector(`[data-item="${id}"]`));

describe("a turn's work folded under one line", () => {
  it("is drawn exactly as before when the setting is off", () => {
    render(view(turn));
    expect(folds()).toHaveLength(0);
    for (const id of ["s1", "t1", "s2", "t2", "s3"]) expect(document.querySelector(`[data-item="${id}"]`), id).not.toBeNull();
  });

  it("keeps only the last sentence outside, and the rest in a closed group that is still in the page", () => {
    render(view(turn, { foldWork: true }));
    expect(folds()).toHaveLength(1);
    expect(folds()[0].open).toBe(false);
    for (const id of ["s1", "t1", "s2", "t2"]) expect(inside(id), id).toBe(true);
    expect(inside("s3")).toBe(false);
    expect(document.querySelector('[data-item="s3"]')).not.toBeNull();
    expect(inside("u1")).toBe(false);
  });

  it("opens on click and stays as the person left it when the transcript changes", () => {
    const { rerender } = render(view(turn, { foldWork: true }));
    const group = folds()[0];
    group.open = true;
    fireEvent(group, new Event("toggle"));
    rerender(view([...turn, { t: "say", id: "s4", text: "one more", done: true }], { foldWork: true }));
    expect(folds()[0].open).toBe(true);
  });

  it("leaves the turn that is still running open, and folds it when it settles", () => {
    const { rerender } = render(view(turn, { foldWork: true, turnRunning: true }));
    expect(folds()).toHaveLength(0);
    rerender(view(turn, { foldWork: true, turnRunning: false }));
    expect(folds()).toHaveLength(1);
  });

  it("marks the line when something in the group failed", () => {
    render(view([turn[0], turn[1], tool("bad", "exit 1"), turn[5]], { foldWork: true }));
    expect(folds()[0].hasAttribute("data-failed")).toBe(true);
  });

  it("folds an earlier turn and leaves the running one open", () => {
    const next: Item[] = [{ t: "user", id: "u2", text: "next", authoredTurn: 2, msgIndex: 4 }, { t: "say", id: "n1", text: "on it", done: true }, tool("n-t"), { t: "say", id: "n2", text: "half", done: true }];
    render(view([...turn, ...next], { foldWork: true, turnRunning: true }));
    expect(folds()).toHaveLength(1);
    expect(inside("n1")).toBe(false);
    expect(inside("s1")).toBe(true);
  });

  it("is opened by find and by the graph's landing when the hit is inside, and the state sticks", () => {
    const { rerender } = render(view(turn, { foldWork: true }));
    const group = folds()[0];
    expect(group.open).toBe(false);
    landingBox(group.querySelector<HTMLElement>('[data-item="s2"]')!);
    fireEvent(group, new Event("toggle"));
    expect(folds()[0].open).toBe(true);
    rerender(view([...turn], { foldWork: true }));
    expect(folds()[0].open).toBe(true);
  });

  it("keeps the per-message actions of a sentence inside the group, closed or open", () => {
    render(view(turn, { foldWork: true }));
    const inner = document.querySelector('details.work-fold [data-item="s2"]')!;
    expect(inner.querySelector(".acts, .copy")).not.toBeNull();
  });

  it("keeps the person's own message, and so its rewind and edit controls, outside the group", () => {
    render(view(turn, { foldWork: true }));
    const user = document.querySelector('[data-item="u1"]')!;
    expect(folds()[0].contains(user)).toBe(false);
    expect(user.querySelector("button")).not.toBeNull();
  });

  it("leaves a turn that ended on thinking alone with its answer outside", () => {
    const ended: Item[] = [...turn, { t: "say", id: "th", text: "", reasoning: "weighing it", done: true }];
    render(view(ended, { foldWork: true }));
    expect(inside("s3")).toBe(false);
    expect(inside("th")).toBe(false);
  });
});
