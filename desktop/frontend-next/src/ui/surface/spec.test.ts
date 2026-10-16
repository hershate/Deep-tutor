import { describe, expect, it } from "vitest";
import { parseSurface, validSurfaceValue } from "./spec";
import type { SurfaceField } from "../../port/wire";

const choice: SurfaceField = { type: "multichoice", required: true, options: [{ value: "fast", label: "Fast", preview: "Short run" }, { value: "full", label: "Full" }] };
const valid = { spec_version: 1, body: [{ kind: "stack", children: [{ kind: "text", value: "Settings" }, { kind: "choice_card", key: "mode", label: "Mode", field: choice }] }] };

describe("surface contract", () => {
  it("accepts drafts and plain text differences with closed properties", () => {
    const draft = { kind: "editable_text", key: "draft", label: "Draft", field: { type: "string", default: "  draft\n" } };
    const diff = { kind: "diff_preview", diff: { before: "", after: "  after\n<script>" } };
    expect(parseSurface({ spec_version: 1, body: [draft, diff] })).not.toBeNull();
    for (const node of [
      { ...draft, field: { type: "number" } },
      { ...diff, diff: { before: "" } },
      { ...diff, diff: { before: "", after: "", apply: true } },
      { ...diff, actionId: "approve" },
      { ...diff, diff: { before: "x".repeat(4001), after: "" } },
    ]) expect(parseSurface({ spec_version: 1, body: [node] })).toBeNull();
  });
  it("accepts a native field tree and bounded typed values", () => {
    expect(parseSurface(valid)).not.toBeNull();
    expect(validSurfaceValue(choice, ["fast", "full"])).toBe(true);
    for (const answer of [[], ["other"], ["fast", "fast"], "fast", null, [1]]) expect(validSurfaceValue(choice, answer)).toBe(false);
    expect(validSurfaceValue({ type: "number", min: 1, max: 5 }, 3)).toBe(true);
    for (const answer of ["3", 0, 6, Infinity, null]) expect(validSurfaceValue({ type: "number", min: 1, max: 5 }, answer)).toBe(false);
    expect(validSurfaceValue({ type: "bool", required: true }, false)).toBe(true);
    expect(validSurfaceValue({ type: "string", max_length: 1 }, "😀")).toBe(true);
  });

  it.each([
    { kind: "html", value: "<script/>" },
    { kind: "button", label: "Approve", actionId: "approve", operation: "approve" },
    { kind: "text", value: "hi", actionId: "" },
    { kind: "text", value: "hi", children: [] },
    { kind: "text", value: "hi", tone: ["ok"] },
    { kind: "text", value: null },
    { kind: "field", key: "count", label: "Count", field: { type: "number", default: "2" } },
    { kind: "field", key: "count", label: "Count", field: { type: ["number"] } },
    { kind: "field", key: "count", label: "Count", field: { type: "bool", options: [] } },
    { kind: "choice_card", key: "count", label: "Count", field: { type: "number" } },
    { kind: "meter", progress: 2 },
  ])("refuses a hostile or ambiguous node: %j", node => {
    expect(parseSurface({ spec_version: 1, body: [node] })).toBeNull();
  });

  it("refuses duplicate fields, oversized trees, unknown properties, and future versions", () => {
    const node = valid.body[0].children[1];
    expect(parseSurface({ spec_version: 1, body: [node, node] })).toBeNull();
    expect(parseSurface({ spec_version: 1, body: Array.from({ length: 201 }, () => ({ kind: "text", value: "x" })) })).toBeNull();
    expect(parseSurface({ ...valid, url: "https://example.com" })).toBeNull();
    expect(parseSurface({ ...valid, spec_version: 2 })).toBeNull();
    let deep: unknown = { kind: "text", value: "x" };
    for (let i = 0; i < 8; i++) deep = { kind: "stack", children: [deep] };
    expect(parseSurface({ spec_version: 1, body: [deep] })).toBeNull();
  });
});
it("accepts opaque action buttons while rejecting executable properties and duplicate ids", () => {
 const button = {kind:"button",label:"Continue",actionId:"continue"};
 expect(parseSurface({spec_version:1,body:[button]})).not.toBeNull();
 expect(parseSurface({spec_version:1,body:[button,button]})).toBeNull();
 expect(parseSurface({spec_version:1,body:[{...button,approve:true}]})).toBeNull();
});
