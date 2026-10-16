// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import "./testkit";
import { Transcript } from "./Transcript";
import { STORAGE, boot } from "../i18n";
import type { Item } from "../state/session";

// Nothing is near but the last block, so every other block is a placeholder
// until a selection asks for it.
beforeAll(() => {
  globalThis.IntersectionObserver = class {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: readonly number[] = [];
    readonly scrollMargin = "";
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  } as unknown as typeof IntersectionObserver;
});

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(STORAGE, "zh");
  boot();
});
afterEach(() => {
  document.getSelection()?.removeAllRanges();
  cleanup();
});

const noop = async () => undefined as never;
const turns = (n: number): Item[] =>
  Array.from({ length: n }, (_, i): Item[] => [
    { t: "user", id: `u${i}`, text: `question ${i}` },
    { t: "say", id: `s${i}`, text: `answer ${i}`, done: true },
  ]).flat();

function transcript(items: Item[]) {
  return (
    <Transcript
      items={items} entering={[]} onEntered={() => {}} revision={1} waiting={{}} scroll={{ current: null }}
      hidden={false} onPinned={() => {}} jump={0} focus={null} onApprove={noop} onFullAccess={noop} onPlan={noop}
      onAnswer={noop} onForget={noop} onExtInvoke={() => {}} onExtSubmit={noop} checkpoints={new Map()}
      onPrepareRewind={noop} onCommitRewind={noop} onPrepareFileRevert={noop}
      onCommitFileRevert={noop} needsProject={false} onOpenProject={() => {}} onKeepHere={() => {}}
    />
  );
}

const chunks = () => [...document.querySelectorAll<HTMLElement>(".chunk")];
const select = async (from: number, to: number) => {
  const all = chunks();
  const range = document.createRange();
  range.setStart(all[from], 0);
  range.setEnd(all[to], 0);
  await act(async () => {
    const sel = document.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    await new Promise((r) => setTimeout(r, 0));
  });
};

describe("a selection that spans blocks nobody is looking at", () => {
  it("holds the blocks it only passes through as plain text, and the blocks its ends sit in whole", async () => {
    render(transcript(turns(300)));
    expect(chunks().length).toBeGreaterThan(5);
    expect(chunks()[3].childElementCount).toBe(0);

    await select(1, 5);

    for (const i of [1, 5]) expect(chunks()[i].querySelector("[data-item]"), `block ${i} holds an end`).not.toBeNull();
    for (const i of [2, 3, 4]) {
      const block = chunks()[i];
      expect(block.querySelector("[data-item]"), `block ${i} is only passed through`).toBeNull();
      expect(block.textContent).toMatch(/question \d+answer \d+question/);
    }
    expect(document.getSelection()!.toString()).toContain("answer");
  });

  it("gives the placeholders back when the selection is cleared", async () => {
    render(transcript(turns(300)));
    await select(1, 5);
    expect(chunks()[3].childElementCount).toBeGreaterThan(0);

    await act(async () => {
      document.getSelection()!.removeAllRanges();
      await new Promise((r) => setTimeout(r, 0));
    });

    for (const i of [1, 2, 3, 4, 5]) expect(chunks()[i].childElementCount, `block ${i}`).toBe(0);
  });

  it("keeps a block's height while it stands in as text, so the page does not move", async () => {
    render(transcript(turns(300)));
    const before = chunks()[3].style.height;
    expect(before).not.toBe("");
    await select(1, 5);
    expect(chunks()[3].style.height).toBe(before);
    expect(chunks()[3].style.overflow).toBe("hidden");
  });
});
