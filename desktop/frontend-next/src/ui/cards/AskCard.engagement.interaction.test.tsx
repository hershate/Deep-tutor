// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AskCard } from "./AskCard";
import type { Item } from "../../state/session";

afterEach(cleanup);
const item = (answered?: string[][]): Extract<Item, { t: "ask" }> => ({
  t: "ask", id: "row", ask: { id: "ask-1", questions: [{ id: "q", prompt: "Pick", options: [{ label: "A" }, { label: "B" }] }] }, answered,
});

it("reports the first interaction without submitting an answer", async () => {
  const onEngage = vi.fn(async () => {});
  const onAnswer = vi.fn(async () => {});
  render(<AskCard item={item()} onAnswer={onAnswer} {...{ onEngage }} />);
  expect(onEngage).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "A" }));
  await userEvent.type(screen.getByRole("textbox", { name: "补充说明（可选）" }), "说明");
  expect(onEngage).toHaveBeenCalledExactlyOnceWith("ask-1");
  expect(onAnswer).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "确认" }));
  expect(onAnswer).toHaveBeenCalledWith("row", "ask-1", [{ questionId: "q", selected: ["A", "说明"] }]);
});

it.each(["wheel", "touchMove", "keyDown", "input"])("protects an ask when the user interacts through %s", async (kind) => {
  const onEngage = vi.fn(async () => {});
  const { container } = render(<AskCard item={item()} onAnswer={vi.fn()} {...{ onEngage }} />);
  const card = container.querySelector(".ask")!;
  fireEvent[kind as "wheel"](card);
  await waitFor(() => expect(onEngage).toHaveBeenCalledWith("ask-1"));
});

it("does not engage a sealed card", () => {
  const onEngage = vi.fn(async () => {});
  const { container } = render(<AskCard item={item([["A"]])} onAnswer={vi.fn()} {...{ onEngage }} />);
  fireEvent.wheel(container.querySelector(".ask")!);
  expect(onEngage).not.toHaveBeenCalled();
});

it("keeps input and retries engagement after a transport failure", async () => {
  const onEngage = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined);
  render(<AskCard item={item()} onAnswer={vi.fn()} {...{ onEngage }} />);
  await userEvent.click(screen.getByRole("button", { name: "A" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("offline"));
  await userEvent.type(screen.getByRole("textbox", { name: "补充说明（可选）" }), "keep");
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(onEngage).toHaveBeenCalledTimes(2);
  expect((screen.getByRole("textbox", { name: "补充说明（可选）" }) as HTMLInputElement).value).toBe("keep");
});
