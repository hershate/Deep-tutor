import type { ReactNode } from "react";
import type { Item } from "../state/session_types";

export type Held = "full" | "text";

/** How each block a selection touches must stay in the DOM: whole when one of
 *  its ends is inside it, as plain text when the selection only passes through. */
export function heldBlocks(range: Range, blocks: Iterable<Element>): Map<number, Held> {
  const held = new Map<number, Held>();
  let i = 0;
  for (const el of blocks) {
    if (range.intersectsNode(el)) held.set(i, el.contains(range.startContainer) || el.contains(range.endContainer) ? "full" : "text");
    i++;
  }
  return held;
}

/** What a far-away block contributes to a selection that spans it: the words
 *  of the conversation, taken from the items rather than from rendered cards. */
export function chunkText(items: Item[]): ReactNode[] {
  const out: ReactNode[] = [];
  for (const it of items) {
    if ((it.t === "user" || it.t === "say") && it.text) out.push(<div key={it.id} style={{ whiteSpace: "pre-wrap" }}>{it.text}</div>);
  }
  return out;
}
