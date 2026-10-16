// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "./testkit";
import { App } from "./App";
import { Nav } from "./Nav";
import { MockHub } from "../port/mock_hub";
import { dropDraft } from "./feedbackdraft";

// The shared setup parks the address on #chat for the chat-surface suites;
// these tests pin the tutor surface and the default, so each starts from the
// bare address a real launch has.
beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  cleanup();
  dropDraft();
  window.location.hash = "";
  localStorage.removeItem("rx-show-session-tabs");
  vi.restoreAllMocks();
});

const navProps = { at: null, onGo: vi.fn(), onFiles: vi.fn(), onHome: vi.fn(), onRail: vi.fn(), onFeedback: vi.fn(), onTutor: vi.fn(), onNew: vi.fn(), feedbackUnread: 0, shown: true };

// The same harness the feedback entry tests ride: a quiet feedback state, so
// the sidebar footer settles and the palette is reachable.
function hub() {
  const h = new MockHub();
  const build = h.portFor.bind(h);
  h.portFor = (rt) => {
    const port = build(rt);
    port.providerSetup = async () => null;
    port.welcomeSeen = async () => true;
    return port;
  };
  const mp = h.machinePort();
  const mine = mp.myFeedback.bind(mp);
  mp.myFeedback = vi.fn(async () => {
    const m = await mine();
    return { ...m, unread: 0, hasNew: false, items: m.items.map((i) => ({ ...i, needsInput: false, unreadReplies: 0 })) };
  });
  return h;
}

// The app is a study tool first: the tutor is the surface the window opens
// on, and the agent chat — Reasonix's full surface — is a page within it.
// These tests pin the hand-offs between the two.
describe("the study tutor surface", () => {
  it("opens on the study pane by default, its pages already in the rail", async () => {
    render(<App hub={hub()} />);
    // The session rail carries the study pane's pages beside chat sessions —
    // one column for both halves of the window.
    expect(await screen.findByRole("navigation", { name: "学习导航" })).toBeTruthy();
    // No conversation is open, so the chat side of the window has nothing to
    // offer yet.
    expect(screen.queryByRole("button", { name: "新建会话" })).toBeNull();
  });

  it("switches to the agent chat through the header's chat button", async () => {
    render(<App hub={hub()} />);
    await screen.findByRole("navigation", { name: "学习导航" });
    fireEvent.click(screen.getByRole("button", { name: "聊天" }));
    // The conversation pane takes the window; the study pane stays mounted
    // behind it rather than being torn down for a surface swap.
    expect(await screen.findByLabelText("Reasonix Studio")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "发送反馈" })).toBeTruthy();
    expect(document.querySelector(".pane-tutor")?.getAttribute("aria-hidden")).toBe("true");
    expect(!!document.querySelector(".panes > .pane[data-active]")).toBe(true);
  });

  it("focuses the tutor pane from the command palette's row, with chat still mounted", async () => {
    render(<App hub={hub()} />);
    await screen.findByRole("navigation", { name: "学习导航" });
    fireEvent.click(screen.getByRole("button", { name: "聊天" }));
    await screen.findByRole("button", { name: "发送反馈" });
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.keyboard("导师");
    // The pane keeps its own heading; the palette row is the one to pick.
    const palette = screen.getByRole("dialog", { name: "搜索与快捷操作" });
    fireEvent.click(within(palette).getByText("学习导师"));
    // The tutor's pane takes the window back; the chat pane stays mounted
    // behind it rather than being torn down for a surface swap.
    expect(await screen.findByRole("navigation", { name: "学习导航" })).toBeTruthy();
    expect(screen.getByLabelText("Reasonix Studio")).toBeTruthy();
  });

  it("carries the rail button's click to the host unchanged", () => {
    const onTutor = vi.fn();
    const { container } = render(<Nav {...navProps} onTutor={onTutor} />);
    const btn = container.querySelector('button[aria-label="学习导师"]')!;
    expect(btn.getAttribute("data-action")).toBe("tutor.open");
    fireEvent.click(btn);
    expect(onTutor).toHaveBeenCalledOnce();
  });

  it("opens the tutor surface from a #tutor address and clears the hash", async () => {
    window.location.hash = "#tutor";
    render(<App hub={hub()} />);
    expect(await screen.findByRole("navigation", { name: "学习导航" })).toBeTruthy();
    expect(window.location.hash).toBe("");
  });

  it("opens the chat surface from a #chat address and clears the hash", async () => {
    window.location.hash = "#chat";
    render(<App hub={hub()} />);
    expect(await screen.findByLabelText("Reasonix Studio")).toBeTruthy();
    // A hand-off that names chat still leaves the study pane mounted in the
    // one window; it is simply not the pane in front.
    expect(document.querySelector(".pane-tutor")?.getAttribute("aria-hidden")).toBe("true");
    expect(window.location.hash).toBe("");
  });

  // The real machine port carries the /host/settings base; a tutor call on it
  // leaves as /host/settings/tutor/... and dies on the host's 404. The surface
  // must read through the baseless tutor port, and this is the pin.
  it("reads the surface's data through the tutor port, not the settings port", async () => {
    const h = hub();
    const mp = h.machinePort();
    mp.tutorPlans = vi.fn(async () => { throw new Error("tutor call on the settings port"); });
    const tp = h.tutorPort();
    const plan = {
      id: "p1", session_id: "s1", title: "端口区分的方案", topic: "端口区分的方案", version: 1,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    };
    tp.tutorPlans = vi.fn(async () => [plan]);
    render(<App hub={h} />);
    expect(await screen.findByRole("navigation", { name: "学习导航" })).toBeTruthy();
    // The plan lands twice in the one window — a run row in the session rail
    // beside chat sessions, and its row in the tutor pane's own list — and
    // both are drawn through the baseless tutor port. The two reads settle
    // at different ticks, so wait for the pair before pinning the ports.
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /端口区分的方案/ }).length).toBe(2);
    });
    await waitFor(() => {
      expect(tp.tutorPlans).toHaveBeenCalled();
      expect(mp.tutorPlans).not.toHaveBeenCalled();
    });
  });
});
