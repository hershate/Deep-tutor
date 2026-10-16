// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { apply } from "./look";
import type { Appearance } from "../port/look";

afterEach(() => { delete document.documentElement.dataset.measure; });

const look = (width: string) => ({ width } as unknown as Appearance);

describe("the line width reaches the page as one attribute", () => {
  it("sets it for the two wide choices and clears it for the reading width", () => {
    apply(look("full"));
    expect(document.documentElement.dataset.measure).toBe("full");
    apply(look("full-all"));
    expect(document.documentElement.dataset.measure).toBe("full-all");
    apply(look("standard"));
    expect(document.documentElement.dataset.measure).toBeUndefined();
    apply(look("wide"));
    expect(document.documentElement.dataset.measure).toBeUndefined();
  });
});
