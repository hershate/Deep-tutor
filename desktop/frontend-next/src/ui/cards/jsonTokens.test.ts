import { describe, expect, it } from "vitest";
import { tokenizeJson } from "./jsonTokens";

const joined = (text: string) => tokenizeJson(text).map((tok) => tok.text).join("");

describe("tokenizeJson", () => {
  it("tells keys from string values, and marks numbers and literals", () => {
    const kinds = tokenizeJson('{\n  "command": "ls -la",\n  "limit": 12.5,\n  "ok": true,\n  "next": null\n}')
      .filter((tok) => tok.kind !== "plain")
      .map((tok) => `${tok.kind}:${tok.text}`);
    expect(kinds).toEqual([
      'key:"command"', 'str:"ls -la"', 'key:"limit"', "num:12.5", 'key:"ok"', "lit:true", 'key:"next"', "lit:null",
    ]);
  });

  it("never loses or reorders a character, whatever the text is", () => {
    for (const text of [
      '{"a": "x\\"y", "b": [1, -2, 3e5]}',
      "not json at all: 12 true \"half",
      "",
      '"only a string"',
      '{"unterminated": "abc',
      "{\n\t\"tab\":\t1\n}",
    ]) {
      expect(joined(text)).toBe(text);
    }
  });

  it("keeps an escaped quote inside its string", () => {
    const strs = tokenizeJson('{"k": "say \\"hi\\" now"}').filter((tok) => tok.kind === "str");
    expect(strs.map((tok) => tok.text)).toEqual(['"say \\"hi\\" now"']);
  });

  it("does not colour digits or words that sit inside a string or a longer word", () => {
    const kinds = tokenizeJson('{"path": "build 12 true v2null"}').map((tok) => tok.kind);
    expect(kinds.filter((kind) => kind === "num" || kind === "lit")).toEqual([]);
  });

  it("colours the elements of an array", () => {
    const kinds = tokenizeJson('["a", 1, false]').filter((tok) => tok.kind !== "plain").map((tok) => tok.kind);
    expect(kinds).toEqual(["str", "num", "lit"]);
  });
});
