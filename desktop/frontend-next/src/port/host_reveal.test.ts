import { afterEach, expect, it, vi } from "vitest";

async function hostOver(bridge: Record<string, unknown>) {
  vi.resetModules();
  vi.stubGlobal("window", { reasonixHost: { shell: "electron", platform: "darwin", titleBar: false, ...bridge } });
  return (await import("./host")).host();
}

afterEach(() => vi.unstubAllGlobals());

it("offers reveal only when the shell can show both a pane's file and a project", async () => {
  expect((await hostOver({ revealPath: async () => null, revealWorkspace: async () => null })).revealsFiles()).toBe(true);
  expect((await hostOver({ revealPath: async () => null })).revealsFiles()).toBe(false);
});

it("offers opening files only with a native bridge and preserves its refusal", async () => {
  expect((await hostOver({})).opensFiles()).toBe(false);
  const openFile = vi.fn().mockResolvedValue({ code: "workspace.file_missing", error: "missing" });
  const host = await hostOver({ openFile });
  expect(host.opensFiles()).toBe(true);
  expect(await host.openFile("/rt/r1", "report.pdf")).toEqual({ code: "workspace.file_missing", error: "missing" });
  expect(openFile).toHaveBeenCalledExactlyOnceWith("/rt/r1", "report.pdf");
});

it("offers the logs folder only when the shell can open it, and passes its refusal through", async () => {
  expect((await hostOver({})).revealsLogs()).toBe(false);
  const refusing = await hostOver({ revealLogs: async () => ({ error: "no such folder" }) });
  expect(refusing.revealsLogs()).toBe(true);
  expect(await refusing.revealLogs()).toEqual({ error: "no such folder" });
  expect(await (await hostOver({ revealLogs: async () => null })).revealLogs()).toBeNull();
});


it("forwards unread badge changes and tolerates a shell without badge support", async () => {
  const setUnreadBadge = vi.fn().mockResolvedValue(undefined);
  const host = await hostOver({ setUnreadBadge });
  await host.setUnreadBadge(true);
  await host.setUnreadBadge(false);
  expect(setUnreadBadge.mock.calls).toEqual([[true], [false]]);
  await expect((await hostOver({})).setUnreadBadge(true)).resolves.toBeUndefined();
});
