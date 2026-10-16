// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceCard } from "./SurfaceCard";
import type { Item } from "../../state/session";

afterEach(cleanup);
const item: Extract<Item, { t: "ask" }> = { t: "ask", id: "row-1", ask: { id: "ask-1", questions: [{ id: "surface", prompt: "Settings", options: [], surface: { spec_version: 1, title: "Settings", body: [
  { kind: "field", key: "count", label: "Count", field: { type: "number", required: true, min: 1, max: 5 } },
  { kind: "field", key: "enabled", label: "Enabled", field: { type: "bool", default: false } },
  { kind: "choice_card", key: "mode", label: "Mode", field: { type: "choice", required: true, options: [{ value: "fast", label: "Fast", description: "Short run", preview: "Native preview" }, { value: "full", label: "Full" }] } },
] } }] } };

it("exposes a surface heading and required inputs to assistive technology", async () => {
  render(<SurfaceCard item={item} onAnswer={vi.fn(async () => {})} />);
  expect(await screen.findByRole("heading", { name: "Settings", level: 3 })).toBeTruthy();
  expect((await screen.findByLabelText("Count")).getAttribute("aria-required")).toBe("true");
});

it("submits typed fields and stable choice ids with existing controls and no rail", async () => {
  const answer = vi.fn(async () => {});
  const view = render(<SurfaceCard item={item} onAnswer={answer} />);
  expect(view.container.querySelector(".line, .g, iframe")).toBeNull();
  fireEvent.change(await screen.findByLabelText("Count"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("radio", { name: /Fast/ }));
  expect(screen.getByText("Native preview")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledWith("row-1", "ask-1", [{ questionId: "surface", selected: [], value: { count: 3, enabled: false, mode: "fast" } }]));
});

it("keeps inputs editable after a refused submission and retries the same values", async () => {
  const answer = vi.fn().mockRejectedValueOnce(new Error("refused")).mockResolvedValueOnce(undefined);
  render(<SurfaceCard item={item} onAnswer={answer} />);
  fireEvent.change(await screen.findByLabelText("Count"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("radio", { name: /Fast/ }));
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect((screen.getByLabelText("Count") as HTMLInputElement).value).toBe("2");
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
});

it("renders expired and settled history without answer controls or entering motion", async () => {
  const answer = vi.fn(async () => {});
  const expired = render(<SurfaceCard item={{ ...item, answered: [], surfaceExpired: true }} onAnswer={answer} />);
  expect(expired.container.querySelector('[data-code="surface.expired"]')).toBeTruthy();
  expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
  expired.unmount();
  render(<SurfaceCard item={{ ...item, answered: [[]], surfaceValues: { count: 2, mode: "fast", enabled: false } }} onAnswer={answer} />);
  expect((await screen.findByLabelText("Count") as HTMLInputElement).value).toBe("2");
  expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
  expect(answer).not.toHaveBeenCalled();
});

it.each(["__proto__", "constructor", "toString"])("submits an accepted field id %s without inherited values", async key => {
  const answer = vi.fn(async (_row: string, _ask: string, _answers: {questionId:string;selected:string[];value?:Record<string,unknown>}[]) => {});
  const named: Extract<Item, {t:"ask"}> = { ...item, ask:{ id:"ask-keys", questions:[{ id:"surface", prompt:"Name", options:[], surface:{ spec_version:1, body:[{ kind:"field", key, label:"Name", field:{type:"string",required:true} }] } }] } };
  render(<SurfaceCard item={named} onAnswer={answer} />);
  expect((await screen.findByLabelText("Name") as HTMLInputElement).value).toBe("");
  fireEvent.change(screen.getByLabelText("Name"), {target:{value:"Alice"}});
  fireEvent.click(screen.getByRole("button",{name:"提交"}));
  await waitFor(()=>expect(answer).toHaveBeenCalledTimes(1));
  const values = answer.mock.calls[0][2][0].value;
  expect(Object.hasOwn(values!,key)).toBe(true);
  expect(JSON.parse(JSON.stringify(values))[key]).toBe("Alice");
});
it("keeps field bounds host-owned and supports native checkbox and card keyboard input", async () => {
  const answer = vi.fn(async () => {});
  render(<SurfaceCard item={item} onAnswer={answer} />);
  const count = await screen.findByLabelText("Count") as HTMLInputElement;
  fireEvent.change(count, {target:{value:"6"}});
  fireEvent.click(screen.getByRole("radio",{name:/Fast/}));
  expect(count.value).toBe("6");
  expect((screen.getByRole("button",{name:"提交"}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(count,{target:{value:"3"}});
  fireEvent.click(screen.getByRole("checkbox",{name:"Enabled"}));
  fireEvent.keyDown(screen.getByRole("radio",{name:/Fast/}),{key:"ArrowRight",code:"ArrowRight"});
  expect(screen.getByRole("radio",{name:"Full"}).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("button",{name:"提交"}));
  await waitFor(()=>expect(answer).toHaveBeenCalledWith("row-1","ask-1",[{questionId:"surface",selected:[],value:{count:3,enabled:true,mode:"full"}}]));
});
