// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import type { Protocol, ProviderEntry } from "../port/port";
import type { Port } from "./Providers";
import { EditConn } from "./EditConn";

afterEach(cleanup);

const wires: Protocol[] = [
  { kind: "openai", discovery: "openai", serverWebSearch: false, reasoningParams: true },
  { kind: "responses", discovery: "openai", serverWebSearch: true, reasoningParams: true },
  { kind: "anthropic", discovery: "anthropic", serverWebSearch: true, reasoningParams: false, effortField: "output_config.effort" },
  { kind: "typesafe", answers: "decision", discovery: "", serverWebSearch: false, reasoningParams: false },
];

const relay = (over: Partial<ProviderEntry> = {}): ProviderEntry => ({
  name: "relay", kind: "openai", baseUrl: "https://relay.invalid/v1",
  models: ["a", "b"], default: "a", hasKey: true, inUse: false, preset: false, ...over,
});

const mount = (entry: ProviderEntry, port: Partial<Port>, declare = false) =>
  render(<EditConn entry={entry} port={port as unknown as Port} busy="" setBusy={() => {}} onDone={() => {}} onRevert={() => {}} declare={declare} />);

it("offers the chat wires and saves the one picked", async () => {
  const editProvider = vi.fn(async () => {});
  mount(relay(), { editProvider, protocols: async () => wires });

  const select = await screen.findByRole("combobox", { name: "接口协议" }) as HTMLSelectElement;
  expect(select.value).toBe("openai");
  expect([...select.options].map((o) => o.value)).toEqual(["openai", "responses", "anthropic"]);
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);

  await userEvent.selectOptions(select, "anthropic");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(editProvider).toHaveBeenCalled());
  expect(editProvider).toHaveBeenCalledWith(expect.objectContaining({ name: "relay", kind: "anthropic" }));
});

it("words the thinking field by the wire being picked, not the stored one", async () => {
  mount(relay({ effortField: "reasoning_effort" }), { editProvider: vi.fn(), protocols: async () => wires }, true);
  const select = await screen.findByRole("combobox", { name: "接口协议" });
  expect(document.body.textContent).toContain("reasoning_effort");
  await userEvent.selectOptions(select, "anthropic");
  expect(document.body.textContent).toContain("output_config.effort");
  expect(document.body.textContent).not.toContain("reasoning_effort");
});

it("checks a model on the wire being picked, not the stored one", async () => {
  const checkProviderModel = vi.fn(async () => ({ ok: true }));
  mount(relay(), { editProvider: vi.fn(), protocols: async () => wires, checkProviderModel } as unknown as Partial<Port>);

  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "接口协议" }), "responses");
  await userEvent.click(screen.getAllByRole("button", { name: /测试/ })[0]);
  await waitFor(() => expect(checkProviderModel).toHaveBeenCalled());
  expect(checkProviderModel).toHaveBeenCalledWith(expect.objectContaining({ kind: "responses" }));
});

it("shows no chooser for a source on a decision wire", async () => {
  mount(relay({ kind: "typesafe" }), { editProvider: vi.fn(), protocols: async () => wires });
  await waitFor(() => expect(screen.getByLabelText("API Key（留空就不动它）")).toBeTruthy());
  expect(screen.queryByRole("combobox", { name: "接口协议" })).toBeNull();
});

it("leaves the wire to the door switch when the account has a sibling door", async () => {
  render(<EditConn entry={relay()} wireFixed port={{ editProvider: vi.fn(), protocols: async () => wires } as unknown as Port} busy="" setBusy={() => {}} onDone={() => {}} onRevert={() => {}} />);
  await waitFor(() => expect(screen.getByLabelText("API Key（留空就不动它）")).toBeTruthy());
  expect(screen.queryByRole("combobox", { name: "接口协议" })).toBeNull();
});

it("still edits when the kernel cannot list wires", async () => {
  const editProvider = vi.fn(async () => {});
  mount(relay(), { editProvider, protocols: async () => { throw new Error("offline"); } });
  expect(screen.queryByRole("combobox", { name: "接口协议" })).toBeNull();
  expect(screen.getByLabelText("API Key（留空就不动它）")).toBeTruthy();
});
