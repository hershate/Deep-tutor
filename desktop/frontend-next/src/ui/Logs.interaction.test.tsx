// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import "./testkit";
import { HttpError } from "../port/port";
import type { StudioLogs } from "../port/version";
import { Logs } from "./Logs";

afterEach(cleanup);

const logs = (crashes: StudioLogs["crashes"], truncated = false): StudioLogs => ({ dir: "C:\\data\\logs", crashDir: "C:\\data\\logs\\crash", crashes, truncated });

it("shows the folder and each crash file waiting in it", async () => {
  render(<Logs port={{ studioLogs: async () => logs([{ name: "host-20261010T230806Z-2.31.0-4412.log", at: "2026-10-10T23:08:06Z", bytes: 8421 }]) }} />);
  await waitFor(() => expect(screen.getByText("C:\\data\\logs")).toBeTruthy());
  expect(document.body.textContent).toContain("host-20261010T230806Z-2.31.0-4412.log");
  expect(document.body.textContent).toContain("8 KB");
});

it("says plainly when nothing was left behind", async () => {
  render(<Logs port={{ studioLogs: async () => logs([]) }} />);
  await waitFor(() => expect(document.body.textContent).toContain("没有留下异常退出的记录"));
});

it("says why a kernel without a shell has no folder to point at", async () => {
  const studioLogs = vi.fn(async () => { throw new HttpError(404, "no logs", { code: "studio.no_logs" }); });
  render(<Logs port={{ studioLogs }} />);
  await waitFor(() => expect(document.body.textContent).toContain("没有窗口外壳"));
});

it("says when older crash files are not listed", async () => {
  render(<Logs port={{ studioLogs: async () => logs([{ name: "host-a.log", at: "2026-10-10T23:08:06Z", bytes: 10 }], true) }} />);
  await waitFor(() => expect(document.body.textContent).toContain("还有更早的记录没有列出"));
});
