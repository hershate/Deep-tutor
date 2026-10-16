// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { AskTimeout } from "./AskTimeout";
import type { AgentPort } from "../port/port";
import type { AskTimeoutSettings } from "../port/boundary";

afterEach(cleanup);

const base: AskTimeoutSettings = { seconds: 0, defaultSeconds: 300, minSeconds: 10, maxSeconds: 86400, path: "/u/config.toml" };

function portWith(save: (s: number) => Promise<AskTimeoutSettings>, initial = base) {
  return { askTimeout: vi.fn(async () => initial), saveAskTimeout: vi.fn(save) } as unknown as AgentPort & { saveAskTimeout: ReturnType<typeof vi.fn> };
}

describe("the ask timeout setting", () => {
  it("ships off, with no number to set until it is on", async () => {
    const port = portWith(async (s) => ({ ...base, seconds: s }));
    render(<AskTimeout port={port} />);
    const sw = await screen.findByRole("switch", { name: "提问超时后自动跳过" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByRole("textbox", { name: "等待时长" })).toBeNull();
  });

  it("turns on at the default wait and off at zero", async () => {
    const port = portWith(async (s) => ({ ...base, seconds: s }));
    render(<AskTimeout port={port} />);
    const sw = await screen.findByRole("switch", { name: "提问超时后自动跳过" });
    await userEvent.click(sw);
    expect(port.saveAskTimeout).toHaveBeenLastCalledWith(300);
    await waitFor(() => expect(sw.getAttribute("aria-checked")).toBe("true"));
    expect((await screen.findByRole("textbox", { name: "等待时长" }) as HTMLInputElement).value).toBe("300");
    await userEvent.click(sw);
    expect(port.saveAskTimeout).toHaveBeenLastCalledWith(0);
    await waitFor(() => expect(sw.getAttribute("aria-checked")).toBe("false"));
  });

  it("commits a new wait once, on Enter", async () => {
    const port = portWith(async (s) => ({ ...base, seconds: s }), { ...base, seconds: 120 });
    render(<AskTimeout port={port} />);
    const field = await screen.findByRole("textbox", { name: "等待时长" });
    await userEvent.clear(field);
    await userEvent.type(field, "45{Enter}");
    expect(port.saveAskTimeout).toHaveBeenCalledTimes(1);
    expect(port.saveAskTimeout).toHaveBeenCalledWith(45);
  });

  it("refuses a wait out of range without writing", async () => {
    const port = portWith(async (s) => ({ ...base, seconds: s }), { ...base, seconds: 120 });
    render(<AskTimeout port={port} />);
    const field = await screen.findByRole("textbox", { name: "等待时长" });
    await userEvent.clear(field);
    await userEvent.type(field, "3{Enter}");
    expect(port.saveAskTimeout).not.toHaveBeenCalled();
    expect(screen.getByText("请输入 10 到 86400 之间的整数。")).toBeTruthy();
  });

  it("keeps the switch where the kernel left it when a save is refused, and says why", async () => {
    const port = portWith(async () => { throw new Error("disk full"); });
    render(<AskTimeout port={port} />);
    const sw = await screen.findByRole("switch", { name: "提问超时后自动跳过" });
    await userEvent.click(sw);
    expect(await screen.findByText(/disk full/)).toBeTruthy();
    expect(sw.getAttribute("aria-checked")).toBe("false");
  });
});

describe("a value the file holds that cannot be used", () => {
  it("is said out loud, not shown as a plain off", async () => {
    const port = portWith(async (s) => ({ ...base, seconds: s }), { ...base, invalid: "ask_timeout.out_of_range" });
    render(<AskTimeout port={port} />);
    expect(await screen.findByText(/配置文件里的提问等待时长无效/)).toBeTruthy();
  });
});
