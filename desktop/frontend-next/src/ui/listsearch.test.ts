import { expect, it } from "vitest";
import { listMatcher } from "./listsearch";

it.each([
  ["READ", ["read_notes"], true],
  ["notes read", ["read_notes", "Project documentation"], true],
  ["  project\tNOTES\n", ["read_notes", "Project documentation"], true],
  ["cafe\u0301", ["café_lookup"], true],
  ["CAFÉ", ["cafe\u0301_lookup"], true],
  ["查找", [undefined, "查找资料"], true],
  ["schema unsupported", ["inspect[raw]", undefined, "Unsupported schema keyword"], true],
  ["inspect[raw]", ["inspect[raw]"], true],
  ["[raw]", ["inspectraw"], false],
  ["read unavailable", ["read_notes", "Project documentation"], false],
  ["   ", [undefined, ""], true],
  ["read", [undefined, ""], false],
] as const)("matches supplied list values for %s", (query, values, expected) => {
  expect(listMatcher(query)(values)).toBe(expected);
});

it("reuses one query across inventories without retaining item values", () => {
  const matches = listMatcher("notes read");
  expect(matches(["read_notes", "Project documentation"])).toBe(true);
  expect(matches(["write_notes", "Update project documentation"])).toBe(false);
  expect(listMatcher("write")(["write_notes"])).toBe(true);
  expect(matches(["read_notes"])).toBe(true);
});
