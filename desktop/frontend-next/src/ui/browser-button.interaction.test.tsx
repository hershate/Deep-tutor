// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { App } from "./App";
import { MockHub } from "../port/mock_hub";
import { boot, STORAGE } from "../i18n";
import { refresh } from "./viewport";

// jsdom lays nothing out, so the width the fold reads is whatever is said here.
function room(width: number) {
  vi.spyOn(document.body, "clientWidth", "get").mockReturnValue(width);
  vi.spyOn(document.body, "clientHeight", "get").mockReturnValue(900);
  refresh();
}

beforeEach(async () => {
  await import("./Settings");
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem(STORAGE, "zh");
  boot();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  room(1440);
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  delete document.documentElement.dataset.fold;
  vi.restoreAllMocks();
});

async function open() {
  const hub = new MockHub();
  const build = hub.portFor.bind(hub);
  const prepared = new WeakSet();
  hub.portFor = (rt) => {
    const port = build(rt);
    if (!prepared.has(port)) {
      prepared.add(port);
      port.providerSetup = async () => null;
      port.welcomeSeen = async () => true;
    }
    return port;
  };
  const view = render(<App hub={hub} />);
  await screen.findByRole("combobox", { name: "任务输入" });
  return { hub, view };
}


const browserButton = () => screen.getByRole("button", { name: "Browser" });

it("the title-bar Browser button opens the column on a browser", async () => {
  await open();
  await userEvent.click(browserButton());
  expect(await screen.findByRole("tab", { name: "浏览器" })).toBeTruthy();
  expect(screen.queryByRole("group", { name: "开始" })).toBeNull();
  await userEvent.click(browserButton());
  expect(browserButton().getAttribute("title")).toBe("打开内置 Browser");
});

it("the Workbench tab opens the column on the launcher, with no browser made for the person", async () => {
  await open();
  await userEvent.click(screen.getByRole("tab", { name: /工作台/ }));
  expect(await screen.findByRole("group", { name: "开始" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "浏览器" })).toBeNull();
});

it("says the button collapses the workbench while the column is open", async () => {
  await open();
  await userEvent.click(browserButton());
  expect(browserButton().getAttribute("title")).toBe("收起工作台");
});
