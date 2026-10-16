// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "./testkit";
import { Workspaces } from "./Workspaces";
import type { TreeWorkspace } from "../port/hub";

afterEach(cleanup);

function draw(over: { reject?: boolean; remembered?: boolean[] } = {}) {
  const names = ["a", "b", "c", "d"];
  const tree: TreeWorkspace[] = names.map((name, i) => ({ root: "/" + name, name, remembered: over.remembered?.[i] ?? true, sessions: [] }));
  const placeWorkspace = over.reject ? vi.fn().mockRejectedValue(new Error("write failed")) : vi.fn().mockResolvedValue(undefined);
  const reload = vi.fn().mockResolvedValue(undefined);
  const onError = vi.fn();
  render(<Workspaces hub={{ placeWorkspace } as never} tree={tree} treeRead runtimes={[]} active=""
    folded={new Set()} onFold={() => {}} reload={reload} onOpen={async () => {}} onFocus={() => {}}
    onClose={async () => {}} liveIds={() => []} runs={{}} onRename={() => {}} onError={onError}
    adder={{ add: () => {}, close: () => {}, at: null } as never} />);
  return { placeWorkspace, reload, onError };
}

const row = (name: string) => screen.getByText(name, { selector: ".wsrow *" }).closest(".wsrow") as HTMLElement;

function transfer() {
  const data = new Map<string, string>();
  return {
    types: [] as string[],
    effectAllowed: "", dropEffect: "",
    setData(kind: string, value: string) { data.set(kind, value); this.types = [...data.keys()]; },
    getData: (kind: string) => data.get(kind) ?? "",
  };
}

// jsdom lays nothing out and its generic events carry no pointer position, so
// the row's box and the pointer's half of it are given explicitly.
function send(type: "dragOver" | "drop", el: HTMLElement, dt: object, half: "top" | "bottom") {
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({ top: 100, height: 40, bottom: 140, left: 0, right: 200, width: 200, x: 0, y: 100, toJSON: () => ({}) });
  const ev = createEvent[type](el, { dataTransfer: dt });
  Object.defineProperty(ev, "clientY", { value: half === "top" ? 105 : 135 });
  fireEvent(el, ev);
  return ev;
}
const over = (el: HTMLElement, dt: object, half: "top" | "bottom") => send("dragOver", el, dt, half);

async function drag(from: string, to: string, half: "top" | "bottom") {
  const dt = transfer();
  fireEvent.dragStart(row(from), { dataTransfer: dt });
  const ev = over(row(to), dt, half);
  send("drop", row(to), dt, half);
  fireEvent.dragEnd(row(from), { dataTransfer: dt });
  return ev;
}

it("drops a project before the one it was released over, and the kernel is told the place", async () => {
  const { placeWorkspace, reload } = draw();
  const ev = await drag("c", "a", "top");
  expect(ev.defaultPrevented).toBe(true);
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(placeWorkspace).toHaveBeenCalledWith("/c", "/a");
});

it("the lower half of a row means after it: before the next project, or at the end for the last", async () => {
  const { placeWorkspace } = draw();
  await drag("a", "b", "bottom");
  await waitFor(() => expect(placeWorkspace).toHaveBeenLastCalledWith("/a", "/c"));
  await drag("a", "d", "bottom");
  await waitFor(() => expect(placeWorkspace).toHaveBeenLastCalledWith("/a", ""));
});

it("a drop that leaves the order as it is asks nothing of the kernel", async () => {
  const { placeWorkspace } = draw();
  await drag("b", "b", "top");
  await drag("b", "a", "bottom");
  await drag("b", "c", "top");
  expect(placeWorkspace).not.toHaveBeenCalled();
});

it("shows where it will land while the pointer is over a row, and clears it on release", () => {
  draw();
  const dt = transfer();
  fireEvent.dragStart(row("c"), { dataTransfer: dt });
  over(row("a"), dt, "top");
  expect(row("a").getAttribute("data-drop")).toBe("before");
  over(row("a"), dt, "bottom");
  expect(row("a").getAttribute("data-drop")).toBe("after");
  fireEvent.dragEnd(row("c"), { dataTransfer: dt });
  expect(row("a").hasAttribute("data-drop")).toBe(false);
});

it("leaves everything else that is dragged over a project row alone", () => {
  const { placeWorkspace } = draw();
  const files = { types: ["Files"], getData: () => "" };
  const ev = over(row("a"), files, "top");
  expect(ev.defaultPrevented).toBe(false);
  fireEvent.drop(row("a"), { dataTransfer: files });
  expect(placeWorkspace).not.toHaveBeenCalled();
});

it("only a remembered project can be dragged or dropped on", () => {
  const { placeWorkspace } = draw({ remembered: [true, false, true, true] });
  expect(row("b").getAttribute("draggable")).not.toBe("true");
  expect(row("a").getAttribute("draggable")).toBe("true");
  const dt = transfer();
  fireEvent.dragStart(row("a"), { dataTransfer: dt });
  expect(over(row("b"), dt, "top").defaultPrevented).toBe(false);
  fireEvent.drop(row("b"), { dataTransfer: dt });
  expect(placeWorkspace).not.toHaveBeenCalled();
});

it("reports a host failure without reloading as if the order had changed", async () => {
  const { reload, onError } = draw({ reject: true });
  await drag("c", "a", "top");
  await waitFor(() => expect(onError).toHaveBeenCalledOnce());
  expect(reload).not.toHaveBeenCalled();
});
