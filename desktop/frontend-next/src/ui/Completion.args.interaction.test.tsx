// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { CompletionMenu } from "./Completion";
import type { CompletionItem } from "../port/model";

afterEach(cleanup);

const items: CompletionItem[] = [
  { label: "/mcp__docs__summarize", insert: "/mcp__docs__summarize ", hint: "summarize docs", kind: "prompt",
    args: [{ name: "topic", description: "what to cover", required: true }, { name: "length" }], moreArgs: 3 },
  { label: "/model", insert: "/model ", hint: "换模型", descend: true, kind: "builtin" },
];

const draw = (onPick = vi.fn()) =>
  render(<CompletionMenu id="m" items={items} active={0} kind="slash" query="" kb={false} onPick={onPick} onHover={() => {}} />);

it("lists a prompt's declared arguments under its row, required ones marked", () => {
  draw();
  const rows = screen.getAllByRole("option");
  expect(rows[0].textContent).toContain("topic 必填 · what to cover");
  expect(rows[0].textContent).toContain("length 可选");
  expect(rows[0].textContent).toContain("还有 3 项");
});

it("leaves rows without declarations as they were and still picks the row", async () => {
  const onPick = vi.fn();
  draw(onPick);
  const rows = screen.getAllByRole("option");
  expect(rows[1].textContent).not.toMatch(/必填|可选/);
  await userEvent.pointer({ keys: "[MouseLeft>]", target: rows[0] });
  expect(onPick).toHaveBeenCalledWith(items[0]);
});
