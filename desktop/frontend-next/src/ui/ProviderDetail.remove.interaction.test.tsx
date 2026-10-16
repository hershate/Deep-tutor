// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import type { ProviderEntry } from "../port/port";
import type { Account, Port } from "./Providers";
import { ProviderDetail } from "./ProviderDetail";

afterEach(cleanup);

// Removing a source drops its stored key — the one thing the sheet cannot
// put back — so the header's delete asks first, on the sheet's confirm strip.
it("asks provider deletion on a confirm strip, and removes only when taken", async () => {
  const user = userEvent.setup();
  const onRemove = vi.fn();
  const port = {
    checkProvider: vi.fn(async () => ({ ok: true, models: [] })),
    editProvider: vi.fn(async () => {}),
  } as unknown as Port;
  const entry: ProviderEntry = {
    name: "deepseek-flash",
    kind: "responses",
    baseUrl: "https://api.deepseek.com",
    models: ["deepseek-flash"],
    default: "deepseek-flash",
    hasKey: true,
    inUse: false,
    preset: true,
    canSetVision: false,
    visionModels: [],
    visionSettable: [],
  };
  const a: Account = {
    key: "api.deepseek.com", label: "DeepSeek", host: "https://api.deepseek.com",
    hint: "deepseek-flash", derived: "DeepSeek", byKind: { responses: entry }, kinds: ["responses"],
  };
  render(
    <ProviderDetail
      a={a} port={port} busy="" setBusy={() => undefined} kind="responses"
      onProtocol={() => undefined} onRemove={onRemove} removeFailed=""
      onRename={() => undefined} onEdited={() => undefined} onFailed={() => undefined} onDirty={() => undefined}
    />,
  );

  await user.click(screen.getByRole("button", { name: "删除" }));
  const strip = screen.getByText("删除 DeepSeek？密钥与设置会一并移除。").closest(".confirm") as HTMLElement;
  await user.click(within(strip).getByRole("button", { name: "取消" }));
  expect(screen.queryByText(/密钥与设置会一并移除/)).toBeNull();
  expect(onRemove).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除" }));
  const ask = screen.getByText(/密钥与设置会一并移除/).closest(".confirm") as HTMLElement;
  await user.click(within(ask).getByRole("button", { name: "删除" }));
  expect(onRemove).toHaveBeenCalledTimes(1);
  expect(onRemove).toHaveBeenCalledWith("deepseek-flash");
  expect(screen.queryByText(/密钥与设置会一并移除/)).toBeNull();
});
