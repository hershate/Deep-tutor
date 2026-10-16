// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import type { ModelEntry, ProviderEntry } from "../port/port";
import type { Port } from "./Providers";
import { EditConn } from "./EditConn";
import { ModelChoice } from "./ModelChoice";
import { Picker } from "./Menu";
import { modelMenu } from "./modelmenu";

afterEach(cleanup);

it("shows model aliases while search and selection retain exact provider/model identities", async () => {
  const models = [
    { ref: "local/vendor/long-v1", provider: "local", model: "vendor/long-v1", modelAlias: "Short" },
    { ref: "other/vendor/long-v1", provider: "other", model: "vendor/long-v1", modelAlias: "Other alias" },
    { ref: "local/second", provider: "local", model: "second", modelAlias: "Short" },
  ] as ModelEntry[];
  const onPick = vi.fn();
  render(<Picker label="Models" items={modelMenu(models, [], "local/second")} current="local/second" onPick={onPick} place="top" searchAlways />);
  await userEvent.click(screen.getByRole("button", { name: "Models" }));
  expect(screen.getAllByRole("menuitem")[0].getAttribute("data-value")).toBe("local/second");
  expect(screen.getAllByRole("menuitem")[0].hasAttribute("data-on")).toBe(true);
  expect(screen.getAllByRole("menuitem", { name: /Short/ })).toHaveLength(2);
  const search = screen.getByRole("textbox");
  await userEvent.type(search, "long-v1");
  expect(screen.getByRole("menuitem", { name: /Short/ })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: /Other alias/ })).toBeTruthy();
  expect(document.querySelector('[data-value="local/second"]')).toBeNull();
  await userEvent.clear(search);
  await userEvent.type(search, "Other alias{enter}");
  expect(onPick).toHaveBeenCalledWith("other/vendor/long-v1");
});

it("edits aliases alongside model settings, preserves them across fetch, and retries failed saves", async () => {
  const editProvider = vi.fn().mockRejectedValueOnce(new Error("disk unavailable")).mockResolvedValue(undefined);
  const onDone = vi.fn();
  const entry = {
    name: "local", kind: "openai", baseUrl: "https://local.invalid/v1",
    models: ["vendor/long-v1"], default: "vendor/long-v1", modelAliases: { "vendor/long-v1": "Old" },
    hasKey: true, inUse: false, preset: false,
  } as ProviderEntry;
  const port = {
    editProvider, protocols: vi.fn(async () => []),
    checkProvider: vi.fn(async () => ({ ok: true, models: ["vendor/long-v1", "second"] })),
  } as unknown as Port;
  render(<EditConn entry={entry} port={port} busy="" setBusy={() => {}} onDone={onDone} onRevert={() => {}} />);
  const alias = screen.getByRole("textbox", { name: "vendor/long-v1 的显示别名" });
  expect((alias as HTMLInputElement).value).toBe("Old");
  await userEvent.clear(alias);
  await userEvent.type(alias, "Short");
  const search = screen.getByRole("searchbox", { name: "搜索或添加模型" });
  await userEvent.type(search, "Short");
  expect(screen.getByRole("textbox", { name: "vendor/long-v1 的显示别名" })).toBe(alias);
  await userEvent.clear(search);
  await userEvent.click(screen.getByRole("button", { name: "从服务商读取可用模型" }));
  expect((alias as HTMLInputElement).value).toBe("Short");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  await screen.findByText("保存失败");
  expect(onDone).not.toHaveBeenCalled();
  expect(entry.modelAliases).toEqual({ "vendor/long-v1": "Old" });
  expect((alias as HTMLInputElement).value).toBe("Short");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  expect(editProvider).toHaveBeenLastCalledWith(expect.objectContaining({
    name: "local", models: ["vendor/long-v1"], default: "vendor/long-v1", modelAliases: { "vendor/long-v1": "Short" },
  }));
  await userEvent.clear(alias);
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(editProvider).toHaveBeenLastCalledWith(expect.objectContaining({ modelAliases: {} }));
});

it.each(["enter", "button"])("allows adding an exact wire ID that matches another model's alias via %s", async (method) => {
  const editProvider = vi.fn(async () => {});
  const entry: ProviderEntry = {
    name: "local", kind: "openai", baseUrl: "https://local.invalid/v1", models: ["vendor/long-v1"],
    default: "vendor/long-v1", modelAliases: { "vendor/long-v1": "beta" }, hasKey: true, inUse: false, preset: false,
  };
  const port = { editProvider, protocols: vi.fn(async () => []) } as unknown as Port;
  render(<EditConn entry={entry} port={port} busy="" setBusy={() => {}} onDone={() => {}} onRevert={() => {}} />);
  const search = screen.getByRole("searchbox", { name: "搜索或添加模型" });
  await userEvent.type(search, "beta");
  expect(screen.getByText("vendor/long-v1")).toBeTruthy();
  const add = screen.getByRole("button", { name: /添加未列出的模型「beta」/ });
  if (method === "enter") await userEvent.type(search, "{enter}");
  else await userEvent.click(add);
  expect(screen.getByRole("checkbox", { name: "选用 beta" }).getAttribute("aria-checked")).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(editProvider).toHaveBeenCalledWith(expect.objectContaining({
    models: ["vendor/long-v1", "beta"], default: "vendor/long-v1", modelAliases: { "vendor/long-v1": "beta" },
  }));
});

it("keeps an unchecked alias when its model is selected again", async () => {
  const editProvider = vi.fn(async () => {});
  const entry: ProviderEntry = {
    name: "local", kind: "openai", baseUrl: "https://local.invalid/v1", models: ["raw-model", "second"],
    default: "raw-model", hasKey: true, inUse: false, preset: false,
  };
  const port = { editProvider, protocols: vi.fn(async () => []) } as unknown as Port;
  render(<EditConn entry={entry} port={port} busy="" setBusy={() => {}} onDone={() => {}} onRevert={() => {}} />);
  const alias = screen.getByRole("textbox", { name: "raw-model 的显示别名" }) as HTMLInputElement;
  expect(alias.value).toBe("");
  await userEvent.type(alias, "Short");
  await userEvent.click(screen.getByRole("checkbox", { name: "选用 raw-model" }));
  expect(alias.disabled).toBe(true);
  await userEvent.click(screen.getByRole("checkbox", { name: "选用 raw-model" }));
  expect(alias.value).toBe("Short");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(editProvider).toHaveBeenCalledWith(expect.objectContaining({ modelAliases: { "raw-model": "Short" } }));
});


it("treats special model IDs as alias data without reading object prototypes", async () => {
  const onAlias = vi.fn();
  render(<ModelChoice models={["__proto__", "constructor"]} picked={["__proto__", "constructor"]} vision={[]}
    aliases={{}} onAlias={onAlias} onToggle={() => {}} onAdd={() => {}} />);
  const field = screen.getByRole("textbox", { name: "__proto__ 的显示别名" }) as HTMLInputElement;
  expect(field.value).toBe("");
  await userEvent.type(field, "X");
  expect(onAlias).toHaveBeenCalledWith("__proto__", "X");
});
