// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { SseHub } from "./hub";

afterEach(() => vi.unstubAllGlobals());

it("keeps machine reads and writes independent of runtime IDs and reconnects", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ memories: [] })));
  vi.stubGlobal("fetch", fetcher);
  const hub = new SseHub();
  const machine = hub.machinePort();
  hub.portFor({ id: "r1", base: "/rt/r1", root: "/remote", name: "Remote", host: "gpu" });
  await machine.memories();
  await machine.forgetMemory("local-fact");
  hub.portFor({ id: "r2", base: "/rt/r2", root: "/local", name: "Local" });
  expect(hub.machinePort()).toBe(machine);
  await machine.memories();
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    "/host/settings/memory", "/host/settings/memory/forget", "/host/settings/memory",
  ]);
});


it("pins workspace reads and mutations to the same local owner", async () => {
  const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify({ memories: [], revisions: [], models: [] })));
  vi.stubGlobal("fetch", fetcher);
  const hub = new SseHub();
  const local = hub.machinePort("r2");
  expect(hub.machinePort("r2")).toBe(local);
  expect(hub.machinePort("r1")).not.toBe(local);
  expect(hub.machinePort()).not.toBe(local);
  await local.memories();
  await local.forgetMemory("fact");
  await local.memoryRevisions("fact");
  await local.restoreMemory("fact", 1);
  await local.models("all");
  await local.setDefaultModel("plugin/project/cloud/chat");
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    "/host/settings/runtime/r2/memory", "/host/settings/runtime/r2/memory/forget",
    "/host/settings/runtime/r2/memory/revisions?name=fact", "/host/settings/runtime/r2/memory/restore",
    "/host/settings/runtime/r2/models?answers=all", "/host/settings/runtime/r2/default-model",
  ]);
  fetcher.mockResolvedValueOnce(new Response("refused", { status: 500 }));
  await expect(hub.close("r2")).rejects.toThrow();
  expect(hub.machinePort("r2")).toBe(local);
  await hub.close("r2");
  expect(hub.machinePort("r2")).not.toBe(local);
});
