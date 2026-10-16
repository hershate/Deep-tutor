// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import "./testkit";
import { Versions } from "./Versions";
import type { UpdateProgress, VersionHub } from "../port/port";

afterEach(cleanup);

const row = (version: string, current: boolean) => ({ version, tag: `studio-v${version}`, publishedAt: "", hasNotes: false, current, older: false });

const pulled: VersionHub = {
  current: "2.36.0",
  pinned: "",
  stalePin: false,
  latest: "2.37.0",
  newer: true,
  versions: [row("2.37.0", false), row("2.36.0", true)],
  withdrawn: { version: "2.36.0", reason: "blank window on a fresh folder" },
};

const portFor = (hub: VersionHub) => ({
  versions: () => Promise.resolve(hub),
  pinVersion: () => Promise.resolve(),
  goToVersion: () => Promise.resolve(),
  restartToVersion: () => Promise.resolve(),
  onUpdateProgress: (cb: (p: UpdateProgress) => void) => {
    cb({ version: "", phase: "idle", received: 0, total: 0 });
    return () => {};
  },
});

describe("a running version the publisher pulled", () => {
  it("is named, with the publisher's reason and the version to move to", async () => {
    render(<Versions port={portFor(pulled)} />);
    const banner = (await screen.findByText("当前版本 2.36.0 已被撤回")).closest(".find");
    expect(banner?.textContent).toContain("blank window on a fresh folder");
    expect(banner?.textContent).toContain("2.37.0");
    expect(screen.queryByText("有新版本 2.37.0")).toBeNull();
  });

  it("says nothing of withdrawal for a version that was never pulled", async () => {
    render(<Versions port={portFor({ ...pulled, current: "2.35.0", withdrawn: undefined, versions: [row("2.37.0", false), row("2.35.0", true)] })} />);
    await screen.findByText("有新版本 2.37.0");
    expect(screen.queryByText(/已被撤回/)).toBeNull();
  });
});
