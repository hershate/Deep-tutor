// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import "./testkit";
import { DOCK, Gutter, RAIL } from "./Gutter";

const pointer = (type: string, x: number) => new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, button: 0 }) as MouseEvent & { pointerId: number };
const send = (el: Element, type: string, x: number) => {
  const ev = pointer(type, x);
  Object.defineProperty(ev, "pointerId", { value: 1 });
  fireEvent(el, ev);
};

function mount(open: boolean, edge: "l" | "r" = "r") {
  const span = edge === "l" ? RAIL : DOCK;
  const onOpen = vi.fn();
  const onWidth = vi.fn();
  const view = render(
    <div className="app">
      <section className="pane">
        <Gutter edge={edge} span={span} width={span.def} label="resize" open={open} onWidth={onWidth} onOpen={onOpen} />
      </section>
    </div>,
  );
  const bar = view.container.querySelector(".gutter") as HTMLElement;
  const grip = bar.querySelector(".grip") as HTMLElement;
  return { onOpen, onWidth, bar, grip, app: view.container.querySelector(".app") as HTMLElement };
}

beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});
afterEach(cleanup);

describe.each(["l", "r"] as const)("the %s column's drag handle", (edge) => {
  const renderHandle = (open: boolean) => mount(open, edge);
  const widening = edge === "l" ? 1 : -1;
  it("opens a shut column when its grip is clicked", () => {
    const { grip, onOpen, app } = renderHandle(false);
    send(grip, "pointerdown", 500);
    send(grip, "pointerup", 500);
    fireEvent.click(grip);
    expect(onOpen).toHaveBeenCalledWith(true);
    expect(app.dataset.drag).toBeUndefined();
  });

  it("starts a drag from the grip only once the press has travelled", () => {
    const { grip, bar, app, onOpen } = renderHandle(false);
    send(grip, "pointerdown", 500);
    send(grip, "pointermove", 498);
    expect(app.dataset.drag).toBeUndefined();
    send(grip, "pointermove", 500 + widening * 120);
    expect(app.dataset.drag).toBe("pointer");
    send(bar, "pointermove", 500 + widening * 130);
    send(bar, "pointerup", 500 + widening * 130);
    expect(app.dataset.drag).toBeUndefined();
    expect(onOpen).toHaveBeenCalledWith(true);
  });

  it("still drags from the strip beside the grip", () => {
    const { bar, app, onWidth } = renderHandle(true);
    send(bar, "pointerdown", 500);
    expect(app.dataset.drag).toBe("pointer");
    send(bar, "pointermove", 500 + widening * 50);
    send(bar, "pointerup", 500 + widening * 50);
    expect(onWidth).toHaveBeenCalledWith((edge === "l" ? RAIL : DOCK).def + 50);
  });

  it("toggles from the keyboard", () => {
    const { bar, onOpen } = renderHandle(true);
    fireEvent.keyDown(bar, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith(false);
  });

  it("closes from the grip without changing the remembered width", () => {
    const { grip, onOpen, onWidth } = renderHandle(true);
    fireEvent.click(grip);
    expect(onOpen).toHaveBeenCalledWith(false);
    expect(onWidth).not.toHaveBeenCalled();
  });

  it("supports keyboard sizing and restoring the default width", () => {
    const { bar, onWidth } = renderHandle(true);
    const span = edge === "l" ? RAIL : DOCK;
    fireEvent.keyDown(bar, { key: widening === 1 ? "ArrowRight" : "ArrowLeft" });
    expect(onWidth).toHaveBeenLastCalledWith(span.def + 12);
    fireEvent.keyDown(bar, { key: "Home" });
    expect(onWidth).toHaveBeenLastCalledWith(span.def);
  });
});
