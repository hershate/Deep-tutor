// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useRef } from "react";
import "./testkit";
import { usePlaceholderFit } from "./placeholderfit";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Box({ width }: { width: number }) {
  const box = useRef<HTMLTextAreaElement>(null);
  const placeholder = usePlaceholderFit(box, ["a long placeholder", "short", "…"]);
  return <textarea ref={(el) => { box.current = el; if (el) Object.defineProperty(el, "clientWidth", { value: width, configurable: true }); }} placeholder={placeholder} aria-label="box" />;
}

describe("the placeholder that fits", () => {
  const measure = () => vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ font: "", measureText: (t: string) => ({ width: t.length * 10 }) } as never);

  it("keeps the whole text while it fits", () => {
    measure();
    render(<Box width={400} />);
    expect(screen.getByLabelText("box").getAttribute("placeholder")).toBe("a long placeholder");
  });

  it("falls back to the shorter text, then to the ellipsis, instead of being cut", () => {
    measure();
    render(<Box width={80} />);
    expect(screen.getByLabelText("box").getAttribute("placeholder")).toBe("short");
    cleanup();
    render(<Box width={20} />);
    expect(screen.getByLabelText("box").getAttribute("placeholder")).toBe("…");
  });
});
