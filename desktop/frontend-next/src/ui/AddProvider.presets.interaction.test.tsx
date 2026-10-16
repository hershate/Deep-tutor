// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import type { Port } from "./Providers";
import { AddProvider } from "./AddProvider";
import { HttpError } from "../port/http_error";

afterEach(cleanup);

const presets = [
  { id: "kimi-cn", label: "Kimi CN API", kind: "openai", baseUrl: "https://api.moonshot.cn/v1" },
  { id: "kimi-coding-plan", label: "Kimi Coding Plan", kind: "anthropic", baseUrl: "https://api.kimi.com/coding" },
  { id: "stepfun", label: "StepFun", kind: "openai", baseUrl: "https://api.stepfun.com/v1" },
];

function setup(overrides: Partial<Record<keyof Port, unknown>> = {}, taken: string[] = []) {
  const installProviderPreset = vi.fn(async () => ({ names: ["x"] }));
  const saveProvider = vi.fn(async () => {});
  const onDone = vi.fn();
  const port = {
    protocols: vi.fn(async () => [{ kind: "openai", discovery: "openai", serverWebSearch: false, reasoningParams: true }]),
    providerPresets: vi.fn(async () => presets),
    installProviderPreset,
    probeProvider: vi.fn(),
    saveProvider,
    ...overrides,
  } as unknown as Port;
  const view = render(<AddProvider port={port} taken={taken} known={[]} onDone={onDone} onCancel={() => {}} />);
  return { port, installProviderPreset, saveProvider, onDone, view };
}

const openGallery = async () => userEvent.click(await screen.findByRole("button", { name: /从常用服务开始/ }));

it("creates the preset in the kernel from its id, a name and a key, and writes no ad-hoc connection", async () => {
  const { installProviderPreset, saveProvider, onDone } = setup();
  await openGallery();
  await userEvent.click(screen.getByRole("button", { name: /Kimi Coding Plan/ }));
  expect(document.activeElement).toBe(screen.getByLabelText("API Key"));
  await userEvent.type(screen.getByLabelText("API Key"), "sk-fixture");
  await userEvent.click(screen.getByRole("button", { name: "添加来源" }));
  await waitFor(() => expect(installProviderPreset).toHaveBeenCalledWith({ presetId: "kimi-coding-plan", name: "", apiKey: "sk-fixture" }));
  expect(saveProvider).not.toHaveBeenCalled();
  expect(onDone).toHaveBeenCalled();
});

it("sends a name only when the person typed one, and shows the kernel's name refusal beside it", async () => {
  const refuse = new HttpError(409, "taken", { code: "provider.name_taken" });
  const failing = vi.fn(async () => { throw refuse; });
  setup({ installProviderPreset: failing });
  await openGallery();
  await userEvent.click(screen.getByRole("button", { name: /StepFun/ }));
  await userEvent.type(screen.getByLabelText("来源名称"), "mine");
  await userEvent.click(screen.getByRole("button", { name: "添加来源" }));
  await waitFor(() => expect(screen.getByLabelText("来源名称").getAttribute("aria-invalid")).toBe("true"));
  expect(failing).toHaveBeenCalledWith({ presetId: "stepfun", name: "mine", apiKey: "" });
});

it("searches and groups by host, and goes back to the gallery", async () => {
  setup();
  await openGallery();
  expect(screen.getByRole("region", { name: "api.moonshot.cn" })).toBeTruthy();
  await userEvent.type(screen.getByRole("searchbox", { name: "搜索常用服务" }), "coding");
  expect(screen.queryByRole("button", { name: /StepFun/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Kimi Coding Plan/ })).toBeTruthy();
  await userEvent.clear(screen.getByRole("searchbox", { name: "搜索常用服务" }));
  await userEvent.type(screen.getByRole("searchbox", { name: "搜索常用服务" }), "nothing-like-this");
  expect(screen.getByText("没有匹配的服务")).toBeTruthy();
  await userEvent.clear(screen.getByRole("searchbox", { name: "搜索常用服务" }));
  await userEvent.click(screen.getByRole("button", { name: /StepFun/ }));
  await userEvent.click(screen.getByRole("button", { name: "换一个" }));
  expect(await screen.findByRole("button", { name: /从常用服务开始/ })).toBeTruthy();
});

it("says why the list could not be read and retries", async () => {
  const providerPresets = vi.fn().mockRejectedValueOnce(new Error("kernel down")).mockResolvedValue(presets);
  setup({ providerPresets });
  expect((await screen.findByRole("alert")).textContent).toContain("常用服务列表没能读到");
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByRole("button", { name: /从常用服务开始/ })).toBeTruthy();
});

it("says so when the kernel lists nothing, and manual entry still works", async () => {
  setup({ providerPresets: vi.fn(async () => []) });
  expect(await screen.findByText(/没有提供常用服务预设/)).toBeTruthy();
  await userEvent.type(screen.getByLabelText("接口地址"), "https://relay.example/v1");
  expect((screen.getByLabelText("接口地址") as HTMLInputElement).value).toBe("https://relay.example/v1");
});

it("hides the gallery once an address is typed by hand, and fetches again after a reload", async () => {
  const { port, view } = setup();
  await openGallery();
  await userEvent.type(screen.getByLabelText("接口地址"), "https://relay.example/v1");
  expect(screen.queryByRole("button", { name: /从常用服务开始/ })).toBeNull();
  view.unmount();
  render(<AddProvider port={port} taken={[]} known={[]} onDone={() => {}} onCancel={() => {}} />);
  expect(await screen.findByRole("button", { name: /从常用服务开始/ })).toBeTruthy();
  expect(port.providerPresets).toHaveBeenCalledTimes(2);
});

it("calls the port's presets method on the port, so a method that reads this still works", async () => {
  class Kernel {
    private readonly rows = presets;
    async providerPresets() { return this.rows; }
  }
  const kernel = Object.assign(new Kernel(), {
    protocols: vi.fn(async () => []),
    installProviderPreset: vi.fn(),
  }) as unknown as Port;
  render(<AddProvider port={kernel} taken={[]} known={[]} onDone={() => {}} onCancel={() => {}} />);
  expect(await screen.findByRole("button", { name: /从常用服务开始/ })).toBeTruthy();
});
