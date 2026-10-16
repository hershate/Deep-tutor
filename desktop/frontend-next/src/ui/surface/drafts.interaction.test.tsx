// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceCard } from "./SurfaceCard";
import type { Item } from "../../state/session";

afterEach(cleanup);
const item: Extract<Item, { t: "ask" }> = { t: "ask", id: "row", ask: { id: "ask", questions: [{ id: "surface", prompt: "Draft", options: [], surface: { spec_version: 1, body: [
  { kind: "editable_text", key: "draft", label: "Draft", field: { type: "string", required: true, default: "  initial\n" } },
  { kind: "diff_preview", label: "Changes", diff: { before: "unchanged\n--- file\n<script>before</script>\n", after: "unchanged\n+++ file\n<script>after</script>\n" } },
] } }] } };

it("preserves multiline drafts through submission failure, retry and settled replay", async () => {
  const answer = vi.fn().mockRejectedValueOnce(new Error("refused")).mockResolvedValueOnce(undefined);
  const view = render(<SurfaceCard item={item} onAnswer={answer} />);
  const input = await screen.findByLabelText("Draft") as HTMLTextAreaElement;
  expect(input.tagName).toBe("TEXTAREA");
  expect(input.value).toBe("  initial\n");
  const draft = "  edited\n\n@file /command <script>\t ";
  fireEvent.change(input, { target: { value: draft } });
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await screen.findByRole("alert");
  expect(input.value).toBe(draft);
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
  expect(answer.mock.calls[1]).toEqual(["row", "ask", [{ questionId: "surface", selected: [], value: { draft } }]]);
  view.rerender(<SurfaceCard item={{ ...item, answered: [[]], surfaceValues: { draft } }} onAnswer={answer} />);
  expect(input.value).toBe(draft);
  expect(input.readOnly).toBe(true);
  expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
  expect(answer).toHaveBeenCalledTimes(2);
});

it("displays literal diff headers and markup without executable or file controls", async () => {
  const view = render(<SurfaceCard item={item} onAnswer={vi.fn(async () => {})} />);
  const diff = await screen.findByRole("region", { name: "Changes" });
  expect(diff.textContent).toContain("--- file");
  expect(diff.textContent).toContain("+++ file");
  expect(diff.textContent).toContain("<script>after</script>");
  expect(diff.querySelector("script,button,a,iframe")).toBeNull();
  expect(view.container.querySelector(".g,.line")).toBeNull();
  expect(diff.querySelectorAll('.surface-diff-row[data-d="-"]')).toHaveLength(2);
  expect(diff.querySelectorAll('.surface-diff-row[data-d="+"]')).toHaveLength(2);
});

it("dismisses a draft through the existing cancellation path", async () => {
  const answer = vi.fn(async () => {});
  render(<SurfaceCard item={item} onAnswer={answer} />);
  fireEvent.click(await screen.findByRole("button", { name: "暂不回答" }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith("row", "ask", []));
});
