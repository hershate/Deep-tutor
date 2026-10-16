// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { App } from "./App";
import { MockHub } from "../port/mock_hub";

afterEach(cleanup);

function hub() {
  const h = new MockHub();
  const build = h.portFor.bind(h);
  h.portFor = (rt) => {
    const port = build(rt);
    port.providerSetup = async () => null;
    port.welcomeSeen = async () => true;
    return port;
  };
  return h;
}

// The magnifier beside the session filters shows the rail's find box while the
// box has focus, and a press takes that focus away before the click lands.
it("opens the rail's find box and closes it on the next press instead of reopening it", async () => {
  render(<App hub={hub()} />);
  const magnifier = await screen.findByRole("button", { name: "按名称筛选会话" });
  const box = screen.getByRole("searchbox", { name: "搜索会话 / 项目" });

  await userEvent.click(magnifier);
  expect(document.activeElement).toBe(box);
  await userEvent.click(magnifier);
  expect(document.activeElement).not.toBe(box);
  await userEvent.click(magnifier);
  expect(document.activeElement).toBe(box);
});

it("still opens from the keyboard after a pointer close", async () => {
  render(<App hub={hub()} />);
  const magnifier = await screen.findByRole("button", { name: "按名称筛选会话" });
  const box = screen.getByRole("searchbox", { name: "搜索会话 / 项目" });

  await userEvent.click(magnifier);
  await userEvent.click(magnifier);
  magnifier.focus();
  await userEvent.keyboard("{Enter}");
  expect(document.activeElement).toBe(box);

  await userEvent.click(magnifier);
  expect(document.activeElement).not.toBe(box);
  await userEvent.click(magnifier);
  expect(document.activeElement).toBe(box);
});
