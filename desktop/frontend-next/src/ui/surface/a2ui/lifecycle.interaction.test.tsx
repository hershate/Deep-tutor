// @vitest-environment jsdom
import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceCard } from "../SurfaceCard";
import { ExtensionView } from "../../cards/ExtensionView";
import type { Item } from "../../../state/session";

afterEach(cleanup);
const ask = (id: string): Extract<Item, {t:"ask"}> => ({ t: "ask", id, ask: { id, questions: [{ id: "surface", prompt: id, options: [], surface: { spec_version: 1, body: [{ kind: "editable_text", key: "draft", label: id, field: { type: "string", required: true } }] } }] } });

it("keeps library models isolated through StrictMode and disposing another surface", async () => {
  const answer = vi.fn(async () => {});
  const first = ask("first");
  const second = ask("second");
  const view = render(<StrictMode><SurfaceCard key="first" item={first} onAnswer={answer} /><SurfaceCard key="second" item={second} onAnswer={answer} /></StrictMode>);
  fireEvent.change(await screen.findByLabelText("first"), { target: { value: "one" } });
  fireEvent.change(screen.getByLabelText("second"), { target: { value: "two" } });
  view.rerender(<StrictMode><SurfaceCard key="second" item={second} onAnswer={answer} /></StrictMode>);
  expect((screen.getByLabelText("second") as HTMLTextAreaElement).value).toBe("two");
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith("second", "second", [{questionId:"surface",selected:[],value:{draft:"two"}}]));
});

it("retains drafts when an equivalent prompt is replayed without calling answer", async () => {
  const answer = vi.fn(async () => {});
  const item = ask("Draft");
  const view = render(<SurfaceCard item={item} onAnswer={answer} />);
  fireEvent.change(await screen.findByLabelText("Draft"), { target: { value: "  retained\n" } });
  view.rerender(<SurfaceCard item={JSON.parse(JSON.stringify(item))} onAnswer={answer} />);
  expect((screen.getByLabelText("Draft") as HTMLTextAreaElement).value).toBe("  retained\n");
  expect(answer).not.toHaveBeenCalled();
});

it("clears unsent input after a dismissed prompt settles", async () => {
  const answer = vi.fn(async () => {});
  const item = ask("Draft");
  const view = render(<SurfaceCard item={item} onAnswer={answer} />);
  fireEvent.change(await screen.findByLabelText("Draft"), { target: { value: "unsent" } });
  view.rerender(<SurfaceCard item={{ ...item, answered: [] }} onAnswer={answer} />);
  expect((screen.getByLabelText("Draft") as HTMLTextAreaElement).value).toBe("");
  expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
  expect(answer).not.toHaveBeenCalled();
});

it("binds multichoice arrays and makes clearing a required selection invalid", async () => {
  const item = ask("Choices");
  item.ask.questions[0].surface = { spec_version: 1, body: [{ kind: "choice_card", key: "choices", label: "Choices", field: { type: "multichoice", required: true, options: [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }] } }] };
  const answer = vi.fn(async () => {});
  render(<SurfaceCard item={item} onAnswer={answer} />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Alpha" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Beta" }));
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith("Choices", "Choices", [{ questionId: "surface", selected: [], value: { choices: ["a", "b"] } }]));
  await waitFor(() => expect((screen.getByRole("checkbox", { name: "Alpha" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("checkbox", { name: "Alpha" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Beta" }));
  expect((screen.getByRole("button", { name: "提交" }) as HTMLButtonElement).disabled).toBe(true);
});

it("preserves plugin action ids and literal text without importing the unrestricted catalogue", async () => {
  const invoke = vi.fn();
  const view = render(<ExtensionView body={[{kind:"stack",children:[{kind:"markdown",value:'<script>alert(1)</script> [link](https://example.test)'},{kind:"pip",tone:"ok",value:"Ready"},{kind:"button",label:"Run fixture",actionId:"run"}]}]} onAction={invoke} />);
  const button = await screen.findByRole("button", { name: "Run fixture" });
  expect(view.container.querySelector("script,iframe,img,a")).toBeNull();
  expect(invoke).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(invoke).toHaveBeenCalledWith("run");
});
