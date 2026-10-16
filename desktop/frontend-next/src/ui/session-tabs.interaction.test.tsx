// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { App } from "./App";
import { Appearance } from "./Appearance";
import { MockHub } from "../port/mock_hub";
import { MockPort } from "../port/mock";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  // The strip is on by default now; a test that wants it off writes "off"
  // itself, exactly like a window that turned the appearance switch off.
});
afterEach(cleanup);

async function hub(panes = 2) {
  const kernel = new MockHub();
  const build = kernel.portFor.bind(kernel);
  kernel.portFor = (rt) => {
    const port = build(rt);
    port.providerSetup = async () => null;
    return port;
  };
  if (panes > 1) await kernel.open({ sessionPath: "/sessions/second.jsonl" });
  return kernel;
}

function draw(kernel: MockHub) {
  // A remount lands on the tutor surface by design; this suite is about the
  // chat window's tab strip, so every draw launches on the chat address.
  window.location.hash = "#chat";
  return render(<>
    <App hub={kernel} />
    <Appearance port={new MockPort()} theme="light" onTheme={() => {}} contrast="" onContrast={() => {}}
      weight="" onWeight={() => {}} look={{}} onLook={() => {}} reloadThemes={() => {}} />
  </>);
}

const strip = () => screen.queryByRole("tablist", { name: "会话面板" });
const toggle = () => screen.getByRole("switch", { name: "显示会话标签栏" });
const settled = () => waitFor(() => {
  if (!document.querySelector(".crumb b")?.textContent) throw new Error("the active pane has not loaded");
});

describe("session tabs on by default", () => {
  it("shows the strip by default with two sessions open", async () => {
    draw(await hub());
    await settled();
    expect(toggle().getAttribute("aria-checked")).toBe("true");
    // The pinned Study tab rides first, so two conversations read three tabs.
    expect(strip()?.querySelectorAll('[role="tab"]').length).toBe(3);
  });

  it("hides and shows the open tabs immediately through the appearance switch", async () => {
    draw(await hub());
    await settled();
    await userEvent.click(toggle());
    expect(strip()).toBeNull();
    await userEvent.click(toggle());
    await waitFor(() => expect(strip()?.querySelectorAll('[role="tab"]').length).toBe(3));
  });

  it("shows a strip for a single session by default: the pinned study tab rides first", async () => {
    draw(await hub(1));
    await settled();
    // The tutor is a pinned first tab, so one conversation joining it
    // already makes a pair.
    const tabs = strip()?.querySelectorAll('[role="tab"]');
    expect(toggle().getAttribute("aria-checked")).toBe("true");
    expect(tabs?.length).toBe(2);
    expect(tabs?.[0]?.textContent).toContain("学习");
  });

  it("retains both enabled and disabled choices when the interface remounts", async () => {
    const kernel = await hub();
    const first = draw(kernel);
    await settled();
    // Start from the default (on), turn it off, and let the choice survive.
    await userEvent.click(toggle());
    first.unmount();

    const second = draw(kernel);
    await settled();
    expect(toggle().getAttribute("aria-checked")).toBe("false");
    expect(strip()).toBeNull();
    await userEvent.click(toggle());
    second.unmount();

    draw(kernel);
    await settled();
    expect(toggle().getAttribute("aria-checked")).toBe("true");
    expect(strip()?.querySelectorAll('[role="tab"]').length).toBe(3);
  });
});