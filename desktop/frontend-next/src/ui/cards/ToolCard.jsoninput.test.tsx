// @vitest-environment jsdom
import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import "../testkit";
import { ToolCard } from "./ToolCard";

afterEach(cleanup);

const call = (args: unknown) => ({ id: "c1", name: "bash", args: JSON.stringify(args), readOnly: false }) as Parameters<typeof ToolCard>[0]["tool"];
const opened = (c: HTMLElement) => {
  const d = c.querySelector("details")!;
  d.open = true;
  act(() => { d.dispatchEvent(new Event("toggle")); });
};

describe("the input of a call shown in an activity group", () => {
  it("colours keys, strings, numbers and literals", () => {
    const { container } = render(<ToolCard tool={call({ command: "ls", limit: 12, dry: true })} running activity />);
    opened(container);
    const pre = container.querySelector(".activity-call-input pre")!;
    expect([...pre.querySelectorAll(".jt-key")].map((e) => e.textContent)).toEqual(['"command"', '"limit"', '"dry"']);
    expect(pre.querySelector(".jt-str")?.textContent).toBe('"ls"');
    expect(pre.querySelector(".jt-num")?.textContent).toBe("12");
    expect(pre.querySelector(".jt-lit")?.textContent).toBe("true");
  });

  it("is still exactly the JSON the model sent when selected and copied", () => {
    const args = { command: 'echo "a b"', cwd: "/w" };
    const { container } = render(<ToolCard tool={call(args)} running activity />);
    opened(container);
    expect(container.querySelector(".activity-call-input pre")?.textContent).toBe(JSON.stringify(args, null, 2));
  });
});
