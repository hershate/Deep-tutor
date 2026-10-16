// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { RailSearch } from "./railsearch";
import { RemoteHosts } from "./RemoteHosts";
import type { RemoteHost } from "../port/remote";
import type { HubPort } from "../port/hub";

afterEach(cleanup);

const hosts: RemoteHost[] = [
  { name: "gpu", target: "ada@10.0.0.4", status: "idle", workspaces: [] },
  { name: "build", target: "ci@10.0.0.9", status: "idle", workspaces: [] },
];

function Rail() {
  return (
    <RailSearch>
      <RemoteHosts
        hub={{} as HubPort}
        hosts={hosts}
        runtimes={[]}
        active=""
        onOpen={async () => {}}
        onFocus={() => {}}
        reload={async () => {}}
        trees={{}}
        reloadTrees={async () => {}}
        onClose={async () => {}}
        liveIds={() => []}
        readTree={async () => {}}
        onError={() => {}}
      />
    </RailSearch>
  );
}

const group = () => screen.getByRole("treeitem", { name: /远程机器/ });

describe("the remote machines group", () => {
  it("holds every remote machine under one row that counts them", () => {
    render(<Rail />);
    expect(group().getAttribute("aria-level")).toBe("1");
    expect(within(group()).getByText("2 台机器")).toBeTruthy();
    const machine = screen.getByText("gpu").closest('[role="treeitem"]')!;
    expect(machine.getAttribute("aria-level")).toBe("2");
  });

  it("folds all of them away at once and brings them back", async () => {
    render(<Rail />);
    expect(group().getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(group());
    expect(group().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("gpu")).toBeNull();
    expect(screen.queryByText("build")).toBeNull();
    await userEvent.click(group());
    expect(screen.getByText("gpu")).toBeTruthy();
  });

  it("opens while a search is on, so a match is never hidden by the fold", async () => {
    render(<Rail />);
    await userEvent.click(group());
    await userEvent.type(screen.getByRole("searchbox", { name: "搜索会话 / 项目" }), "gpu");
    expect(screen.getByText("gpu")).toBeTruthy();
    expect(screen.queryByText("build")).toBeNull();
  });
});
