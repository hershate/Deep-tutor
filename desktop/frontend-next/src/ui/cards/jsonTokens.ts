export type JsonKind = "key" | "str" | "num" | "lit" | "plain";
export interface JsonToken {
  kind: JsonKind;
  text: string;
}

// A string (and whether a colon follows it, which makes it a key), a literal as a
// whole word, or a number. Everything between matches is plain, so no character
// is ever dropped or reordered, whatever the text is.
const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g;

/** Splits JSON text into coloured runs. Not a parser: text that is not JSON still
 *  comes back whole, with only what looks like a string, number or literal marked. */
export function tokenizeJson(text: string): JsonToken[] {
  const out: JsonToken[] = [];
  let at = 0;
  const plain = (to: number) => {
    if (to > at) out.push({ kind: "plain", text: text.slice(at, to) });
  };
  for (const m of text.matchAll(TOKEN)) {
    const start = m.index ?? 0;
    plain(start);
    if (m[1] !== undefined) {
      out.push({ kind: m[2] !== undefined ? "key" : "str", text: m[1] });
      if (m[2] !== undefined) out.push({ kind: "plain", text: m[2] });
    } else {
      out.push({ kind: /^[-\d]/.test(m[0]) ? "num" : "lit", text: m[0] });
    }
    at = start + m[0].length;
  }
  plain(text.length);
  return out;
}
