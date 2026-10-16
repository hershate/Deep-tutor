// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { Feedback } from "./Feedback";
import { dropDraft } from "./feedbackdraft";
import { MockPort } from "../port/mock";
import { host } from "../port/host";
import type { AgentPort } from "../port/port";

beforeEach(dropDraft);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

async function open(onError = vi.fn()) {
  const port = new MockPort() as unknown as AgentPort;
  port.openExternal = vi.fn(async () => {});
  render(<Feedback port={port} tab="send" onClose={vi.fn()} onError={onError} />);
  await screen.findByText("随反馈一起发送的环境信息");
  return { onError };
}

describe("the logs folder in the feedback form", () => {
  it("is offered when the shell can open it, and opens it", async () => {
    vi.spyOn(host(), "revealsLogs").mockReturnValue(true);
    const reveal = vi.spyOn(host(), "revealLogs").mockResolvedValue(null);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "打开日志文件夹" }));
    expect(reveal).toHaveBeenCalledTimes(1);
  });

  it("is not drawn in a shell that cannot open it", async () => {
    vi.spyOn(host(), "revealsLogs").mockReturnValue(false);
    await open();
    expect(screen.queryByRole("button", { name: "打开日志文件夹" })).toBeNull();
  });

  it("says so when the folder could not be opened", async () => {
    vi.spyOn(host(), "revealsLogs").mockReturnValue(true);
    vi.spyOn(host(), "revealLogs").mockResolvedValue({ error: "the logs folder is gone" });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "打开日志文件夹" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
