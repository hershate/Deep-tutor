// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { lazySurface } from "./lazy";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("isolates a failed renderer chunk and loads it again on explicit retry", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
  const load = vi.fn().mockRejectedValueOnce(new Error("chunk unavailable")).mockResolvedValueOnce({ default: ({ title }: { title: string }) => <h3>{title}</h3> });
  const View = lazySurface<{ title: string }>(load);
  render(<><p>Existing conversation</p><View title="Native surface" /></>);
  const retry = await screen.findByRole("button", { name: "加载失败 · 重试" });
  expect(screen.getByText("Existing conversation")).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(retry);
  expect(await screen.findByRole("heading", { name: "Native surface" })).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});
