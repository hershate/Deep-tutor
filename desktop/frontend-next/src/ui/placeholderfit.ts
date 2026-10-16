import { useEffect, useState, type RefObject } from "react";

let canvas: HTMLCanvasElement | null = null;

function widthOf(el: HTMLElement, text: string): number {
  canvas ??= document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) return 0;
  const cs = getComputedStyle(el);
  ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  return ctx.measureText(text).width;
}

/** usePlaceholderFit picks the longest candidate that fits the box on one line.
 *  A placeholder that wraps is cut by the box's one-line height, and a browser
 *  draws no ellipsis on a textarea's, so the text is chosen by measuring rather
 *  than clipped. The last candidate is the fallback when none fits. */
export function usePlaceholderFit(box: RefObject<HTMLTextAreaElement | null>, candidates: string[]): string {
  const [pick, setPick] = useState(candidates[0]);
  const key = candidates.join("\u0000");
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const list = key.split("\u0000");
    const fit = () => {
      const cs = getComputedStyle(el);
      const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const hit = list.find((c) => widthOf(el, c) <= room);
      setPick(hit ?? list[list.length - 1]);
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [box, key]);
  return pick;
}
