// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../testkit";
import { MockPort } from "../../port/mock";
import type { AgentPort } from "../../port/port";
import { Tutor } from "./Tutor";
import { QuizPanel } from "./QuizPanel";
import type { TutorView } from "./Tutor";

afterEach(cleanup);

function mounted() {
  const port = new MockPort() as unknown as AgentPort;
  const views: TutorView[] = [{ k: "home" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  // Defined after render hands it back; go only runs on a later click.
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));
  return { port, go, views, ...utils };
}

// The pane's page navigation lives in the shell's session rail, not inside
// the pane; a standalone pane switches pages through its host, which is what
// the harness's go stands in for.
it("opens on the goal page", async () => {
  mounted();
  const start = await screen.findByRole("button", { name: "制定学习方案" }) as HTMLButtonElement;
  expect(start.disabled).toBe(true);
});

it("runs a generation end to end: goal → progress steps → plan view", async () => {
  const user = userEvent.setup();
  const { views } = mounted();
  const box = await screen.findByPlaceholderText(/用一段话描述你的学习目标/);
  await user.type(box, "三个月掌握 React");
  await user.click(screen.getByRole("button", { name: "制定学习方案" }));

  // The scripted stream walks two tools then lands done; the sheet switches
  // to the progress view first.
  await screen.findByText("正在生成你的学习方案");
  await waitFor(() => {
    expect(screen.getByText(/理解学习目标/)).toBeTruthy();
  });

  // done switches to the plan view after the redirect beat.
  await waitFor(() => {
    expect(views[0]?.k).toBe("plan");
  }, { timeout: 5000 });
  expect((await screen.findAllByText(/组件与 JSX/)).length).toBeGreaterThan(0);
});

it("shares a plan read-only, then revokes it", async () => {
  const user = userEvent.setup();
  const { views } = mounted();
  const box = await screen.findByPlaceholderText(/用一段话描述你的学习目标/);
  await user.type(box, "三个月掌握 React");
  await user.click(screen.getByRole("button", { name: "制定学习方案" }));
  await waitFor(() => {
    expect(views[0]?.k).toBe("plan");
  }, { timeout: 5000 });

  // Create the share: the header swaps the button for the link surface.
  await user.click(await screen.findByRole("button", { name: "只读分享" }));
  const revoke = await screen.findByRole("button", { name: "撤销分享" });
  expect(screen.getByRole("link", { name: "查看分享页" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "复制链接" })).toBeTruthy();

  // Revoke: the create button comes back and the link surface is gone.
  await user.click(revoke);
  await screen.findByRole("button", { name: "只读分享" });
  expect(screen.queryByRole("link", { name: "查看分享页" })).toBeNull();
});

it("previews a backup file before importing it", async () => {
  const user = userEvent.setup();
  mounted();
  // The mock preview reports the demo store's own (empty) counts; the flow
  // under test is upload → preview card → confirm → summary.
  const input = await screen.findByLabelText("选择备份文件");
  const file = new File(["{\"format\":\"reasonix-tutor-backup\"}"], "backup.json", { type: "application/json" });
  await user.upload(input, file);
  expect(await screen.findByText("这份备份包含：")).toBeTruthy();
  expect(screen.getByText("0 个会话")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: /确认导入/ }));
  await waitFor(() => {
    expect(screen.getByText(/已导入 0 个会话/)).toBeTruthy();
  });
});

it("exports the plan as an Anki deck, its wrong-book, slides, and degraded pdf", async () => {
  const user = userEvent.setup();
  const { port } = mounted();
  const box = await screen.findByPlaceholderText(/用一段话描述你的学习目标/);
  await user.type(box, "三个月掌握 React");
  await user.click(screen.getByRole("button", { name: "制定学习方案" }));
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "导出 Anki 卡片" })).toBeTruthy();
  }, { timeout: 5000 });

  // jsdom has neither blob URLs nor downloads; capture the port calls.
  const calls: Array<[string, boolean | undefined]> = [];
  port.tutorPlanApkg = async (id: string, wrongOnly?: boolean) => {
    calls.push([id, wrongOnly]);
    return new Blob(["pk"], { type: "application/octet-stream" });
  };
  const slideCalls: string[] = [];
  port.tutorPlanSlides = async (id: string) => {
    slideCalls.push(id);
    return "<!DOCTYPE html><html><body>deck</body></html>";
  };
  const pdfCalls: string[] = [];
  port.tutorPlanPDF = async (id: string) => {
    pdfCalls.push(id);
    return { content: "# md", degraded: true };
  };
  HTMLAnchorElement.prototype.click = () => undefined;
  URL.createObjectURL = (() => "blob:mock") as typeof URL.createObjectURL;
  URL.revokeObjectURL = (() => undefined) as typeof URL.revokeObjectURL;

  await user.click(screen.getByRole("button", { name: "导出 Anki 卡片" }));
  await user.click(screen.getByRole("button", { name: "导出错题卡片" }));
  await user.click(screen.getByRole("button", { name: "导出幻灯片" }));
  await user.click(screen.getByRole("button", { name: "导出 PDF" }));
  await waitFor(() => {
    expect(calls).toHaveLength(2);
    expect(slideCalls).toHaveLength(1);
    expect(pdfCalls).toHaveLength(1);
  });
  expect(calls[0]![1]).toBe(false);
  expect(calls[1]![1]).toBe(true);
  expect(calls[0]![0]).toBe(calls[1]![0]);
  expect(slideCalls[0]).toBe(calls[0]![0]);
  expect(pdfCalls[0]).toBe(calls[0]![0]);
});

it("opens the notes page, creates one, and lists it", async () => {
  const user = userEvent.setup();
  const { go } = mounted();
  go({ k: "notes" });
  expect(await screen.findByRole("button", { name: "新建" })).toBeTruthy();
  await user.click(await screen.findByRole("button", { name: "新建" }));
  const title = await screen.findByPlaceholderText("标题");
  await user.type(title, "第一篇笔记");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => {
    expect(screen.getByText("第一篇笔记")).toBeTruthy();
  });
});

it("closing the note editor leaves, not turns into a blank new note", async () => {
  const user = userEvent.setup();
  const { go } = mounted();
  go({ k: "notes" });
  await user.click(await screen.findByRole("button", { name: "新建" }));
  await user.type(await screen.findByPlaceholderText("标题"), "草稿");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => screen.getByRole("button", { name: /草稿/ }));

  await user.click(screen.getByRole("button", { name: /草稿/ }));
  expect(screen.getByRole("button", { name: "更新" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭" }));
  // The editor is gone outright; open(null) used to re-open it as a new note.
  expect(screen.queryByPlaceholderText("正文（Markdown）")).toBeNull();
  expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
});

// Deletion is asked on the page itself: the native confirm box cannot exist
// in an embedded webview — it never shows, and the page waits on its own
// call forever. Cancel keeps the row; only taking the question removes it.
it("asks note deletion on a page overlay, and deletes only when taken", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const del = vi.spyOn(port, "tutorNoteDelete");
  const views: TutorView[] = [{ k: "notes" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));

  await user.click(await screen.findByRole("button", { name: "新建" }));
  await user.type(await screen.findByPlaceholderText("标题"), "要删的笔记");
  await user.click(screen.getByRole("button", { name: "保存" }));
  const row = await screen.findByRole("button", { name: /要删的笔记/ });

  await user.click(screen.getByRole("button", { name: "删除" }));
  const ask = screen.getByRole("dialog");
  expect(ask.textContent).toContain("删除这条笔记？");
  await user.click(within(ask).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(row).toBeTruthy();
  expect(del).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认" }));
  await waitFor(() => {
    expect(del).toHaveBeenCalledTimes(1);
  });
  expect(await screen.findByText(/还没有笔记/)).toBeTruthy();
});

it("asks KB document deletion on a page overlay, and deletes only when taken", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const del = vi.spyOn(port, "tutorKbDelete");
  const views: TutorView[] = [{ k: "kb" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));

  await user.click(await screen.findByRole("button", { name: "添加文本" }));
  await user.type(await screen.findByPlaceholderText("标题（可空，取正文首行）"), "Rust 手册");
  await user.type(screen.getByPlaceholderText("粘贴文本内容"), "所有权是 Rust 的核心概念。");
  await user.click(screen.getByRole("button", { name: "入库" }));
  await screen.findByRole("button", { name: /Rust 手册/ });

  await user.click(screen.getByRole("button", { name: "删除" }));
  const ask = screen.getByRole("dialog");
  expect(ask.textContent).toContain("删除文档「Rust 手册」？");
  await user.click(within(ask).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: /Rust 手册/ })).toBeTruthy();
  expect(del).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认" }));
  await waitFor(() => {
    expect(del).toHaveBeenCalledTimes(1);
  });
  expect(await screen.findByText(/知识库还是空的/)).toBeTruthy();
});

it("asks before cancelling a running generation, and cancels only when taken", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const cancel = vi.spyOn(port, "tutorCancel");
  const views: TutorView[] = [{ k: "home" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));

  await user.type(await screen.findByPlaceholderText(/用一段话描述你的学习目标/), "三个月掌握 React");
  await user.click(screen.getByRole("button", { name: "制定学习方案" }));
  await screen.findByText("正在生成你的学习方案");

  await user.click(screen.getByRole("button", { name: "取消生成" }));
  const ask = screen.getByRole("dialog");
  expect(ask.textContent).toContain("取消后已产生的模型费用不会退回");
  await user.click(within(ask).getByRole("button", { name: "确认" }));
  await waitFor(() => {
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});

it("shows the honest empty state for a session with no pending questions", async () => {
  const port = new MockPort() as unknown as AgentPort;
  const session = await port.tutorCreateSession("目标");
  const utils = render(
    <Tutor port={port} view={{ k: "clarify", sid: session.id }} onGo={() => undefined} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />,
  );
  expect(await utils.findByText("这个会话当前没有待回答的问题。")).toBeTruthy();
});

it("files a text document and asks the knowledge base about it", async () => {
  const user = userEvent.setup();
  const { go } = mounted();
  go({ k: "kb" });
  expect(await screen.findByRole("heading", { name: /知识库/ })).toBeTruthy();
  expect(await screen.findByText(/知识库还是空的/)).toBeTruthy();

  // Text in, parsed straight away; the row shows the parsed status.
  await user.click(screen.getByRole("button", { name: "添加文本" }));
  await user.type(await screen.findByPlaceholderText("标题（可空，取正文首行）"), "Rust 手册");
  await user.type(screen.getByPlaceholderText("粘贴文本内容"), "所有权是 Rust 的核心概念。");
  await user.click(screen.getByRole("button", { name: "入库" }));
  await waitFor(() => {
    expect(screen.getAllByText(/Rust 手册/).length).toBeGreaterThan(0);
  });

  // Ask: the mock answers citing the first context document.
  await user.click(screen.getByRole("tab", { name: "问答" }));
  await user.type(await screen.findByPlaceholderText("就知识库内容提问…"), "什么是所有权？");
  await user.click(screen.getByRole("button", { name: "提问" }));
  await waitFor(() => {
    expect(screen.getByText(/演示回答/)).toBeTruthy();
  });
  // The citation chip names the source doc (the answer body names it too).
  expect(screen.getAllByText(/《Rust 手册》/).length).toBeGreaterThanOrEqual(2);
  // The question lands in the persisted history.
  await waitFor(() => {
    expect(screen.getByText("什么是所有权？")).toBeTruthy();
  });
});

// The chat history's delete asks on the page overlay too, like every other
// removal on the surface.
it("asks KB chat-record deletion on the overlay, and deletes only when taken", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const del = vi.spyOn(port, "tutorKbChatDelete");
  const views: TutorView[] = [{ k: "kb" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));

  await user.click(await screen.findByRole("button", { name: "添加文本" }));
  await user.type(await screen.findByPlaceholderText("标题（可空，取正文首行）"), "Rust 手册");
  await user.type(screen.getByPlaceholderText("粘贴文本内容"), "所有权是 Rust 的核心概念。");
  await user.click(screen.getByRole("button", { name: "入库" }));
  await screen.findByRole("button", { name: /Rust 手册/ });
  await user.click(screen.getByRole("tab", { name: "问答" }));
  await user.type(await screen.findByPlaceholderText("就知识库内容提问…"), "什么是所有权？");
  await user.click(screen.getByRole("button", { name: "提问" }));
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /什么是所有权？/ })).toBeTruthy();
  });

  await user.click(screen.getByRole("button", { name: "删除" }));
  const ask = screen.getByRole("dialog");
  expect(ask.textContent).toContain("删除这条问答记录？");
  await user.click(within(ask).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: /什么是所有权？/ })).toBeTruthy();
  expect(del).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "删除" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认" }));
  await waitFor(() => {
    expect(del).toHaveBeenCalledTimes(1);
  });
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: /什么是所有权？/ })).toBeNull();
  });
});

// ── The home surface's coverage: staged materials, the learning-data card,
//    the review overview, and the per-plan wrong drill. ──────────────────
it("stages material rows and sends them with the goal", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const upload = vi.spyOn(port, "tutorMaterialUpload");
  const confirm = vi.spyOn(port, "tutorMaterialConfirm");
  const addUrl = vi.spyOn(port, "tutorMaterialURL");
  const views: TutorView[] = [{ k: "home" }];
  const tree = (v: TutorView) => (
    <Tutor port={port} view={v} onGo={go} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />
  );
  let rerender: (v: TutorView) => void = () => undefined;
  const go = (v: TutorView) => {
    views[0] = v;
    rerender(v);
  };
  const utils = render(tree(views[0]!));
  rerender = (v) => utils.rerender(tree(v));

  await user.type(await screen.findByPlaceholderText(/用一段话描述你的学习目标/), "三个月学会 Rust");
  // A web page rides the session as fetched text.
  await user.type(screen.getByPlaceholderText("https://"), "https://example.com/rust-book");
  await user.click(screen.getByRole("button", { name: "添加网页" }));
  expect(screen.getByText("https://example.com/rust-book")).toBeTruthy();
  // A file rides it with the parse effort its row chose.
  const file = new File(["# Rust 概览"], "rust.md", { type: "text/markdown" });
  const input = document.querySelector('input[data-action="tutor.mat-file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  await user.selectOptions(screen.getByLabelText("rust.md 的解析方式"), "fast");
  await user.click(screen.getByRole("button", { name: "制定学习方案" }));

  await screen.findByText("正在生成你的学习方案");
  await waitFor(() => {
    expect(upload).toHaveBeenCalledWith(expect.any(String), "rust.md", expect.any(String));
    expect(confirm).toHaveBeenCalledWith(expect.any(String), expect.any(String), "fast");
    expect(addUrl).toHaveBeenCalledWith(expect.any(String), "https://example.com/rust-book");
  });
});

it("renders the learning-data card from the ledger", async () => {
  const port = new MockPort() as unknown as AgentPort;
  const today = new Date();
  const key = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  port.tutorStatsSummary = vi.fn(async () => ({ total_minutes: 120, today_minutes: 30, streak_days: 4, checkin_days: 9 }));
  port.tutorStatsCalendar = vi.fn(async () => ({ [key]: 45 }));
  port.tutorStatsByPlan = vi.fn(async () => [{ plan_id: "p1", topic: "Rust 入门", minutes: 80 }]);
  render(<Tutor port={port} view={{ k: "home" }} onGo={() => undefined} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />);
  expect(await screen.findByText("学习数据")).toBeTruthy();
  expect(screen.getByText("连胜 4 天")).toBeTruthy();
  expect(screen.getByText("打卡 9 天")).toBeTruthy();
  // Twelve weeks of days, one cell each.
  const heat = document.querySelector(".tut-heat")!;
  expect(heat.querySelectorAll("i")).toHaveLength(84);
  expect(heat.querySelector('i[data-level="2"]')).toBeTruthy();
  expect(screen.getByText("Rust 入门")).toBeTruthy();
  expect(screen.getByText("80 分钟")).toBeTruthy();
});

it("shows the day's due and the wrong-book overview with plan jumps", async () => {
  const port = new MockPort() as unknown as AgentPort;
  port.tutorReviewDue = vi.fn(async () => ({ items: [], total: 3 }));
  port.tutorWrongOverview = vi.fn(async () => ({ total: 5, items: [{ plan_id: "p1", topic: "React 基础", count: 5 }] }));
  const goes: TutorView[] = [];
  render(<Tutor port={port} view={{ k: "review" }} onGo={(v) => goes.push(v)} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />);
  expect(await screen.findByText("今日到期 3 项")).toBeTruthy();
  expect(screen.getByText("错题 5 道")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: /React 基础/ }));
  expect(goes[0]).toEqual({ k: "plan", pid: "p1", tab: "quiz" });
});

it("drills a plan's wrong book through the server's judgement", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  port.tutorQuizzes = vi.fn(async () => ({ title: "", quiz_count: 0, quizzes: [] }));
  port.tutorQuizWrong = vi.fn(async () => ({
    total: 1,
    items: [{ id: "q9", plan_id: "p1", node_id: "n1", type: "choice" as const, question: "Rust 的所有权规则是哪个？", options: ["一份数据只有一个所有者", "随便几个所有者"], answer: "一份数据只有一个所有者" }],
  }));
  port.tutorQuizSubmit = vi.fn(async () => ({ correct: true }));
  render(<QuizPanel port={port} pid="p1" onError={() => undefined} />);
  await user.click(await screen.findByRole("button", { name: "取本方案错题" }));
  expect(await screen.findByText(/Rust 的所有权规则是哪个？/)).toBeTruthy();
  await user.click(screen.getByRole("radio", { name: /一份数据只有一个所有者/ }));
  await user.click(screen.getByRole("button", { name: "判定" }));
  expect(await screen.findByText(/出了错题本/)).toBeTruthy();
  expect(port.tutorQuizSubmit).toHaveBeenCalledWith("q9", "p1", "一份数据只有一个所有者");
});

// The write-back fires once every choice is judged, on the verdicts
// themselves — a selection is not a result, and the verdict that just landed
// is part of the set it reads.
it("writes quiz progress back only after every choice is judged, verdicts included", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const quizzes = [
    { id: "c1", node_id: "k1", type: "choice" as const, question: "一加一等于？", options: ["2", "3"], answer: "2" },
    { id: "c2", node_id: "k1", type: "choice" as const, question: "二加一等于？", options: ["3", "4"], answer: "3" },
  ];
  port.tutorQuizzes = vi.fn(async () => ({ title: "", quiz_count: quizzes.length, quizzes }));
  // The first is answered wrong, the second right: the node lands at 0.5.
  port.tutorQuizSubmit = vi.fn(async (qid: string) => ({ correct: qid === "c2", answer: qid === "c2" ? "3" : "2" }));
  const record = vi.spyOn(port, "tutorProgressRecord");
  render(<QuizPanel port={port} pid="p1" onError={() => undefined} />);

  await user.click(await screen.findByRole("radio", { name: "2 3" }));
  await user.click(screen.getAllByRole("button", { name: "判定" })[0]!);
  await waitFor(() => {
    expect(screen.getByText(/再想想/)).toBeTruthy();
  });
  // One judged of two: nothing is written back yet, and a picked-but-unjudged
  // answer does not stand in for one.
  expect(record).not.toHaveBeenCalled();

  await user.click(screen.getByRole("radio", { name: "1 3" }));
  await user.click(screen.getByRole("button", { name: "判定" }));
  await waitFor(() => {
    expect(record).toHaveBeenCalledTimes(1);
  });
  // The second verdict is in the set the score reads: one hit of two.
  expect(record).toHaveBeenCalledWith("p1", "k1", "learning", 0.5);
});

// The wrongs-only mode walks the wrong book by its own index — the due cards
// sitting in front of it in the "all" mode must not clamp it back to the
// first item.
it("walks each wrong quiz once in the wrongs-only review mode", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  port.tutorReviewDue = vi.fn(async () => ({ items: [], total: 2 }));
  port.tutorReviewQueue = vi.fn(async () => ({
    total: 2,
    items: [
      { plan_id: "p1", plan_topic: "React", node_id: "k1", name: "组件", status: "not_started", mastery: 0, next_review: "" },
      { plan_id: "p1", plan_topic: "React", node_id: "k2", name: "状态", status: "not_started", mastery: 0, next_review: "" },
    ],
  }));
  port.tutorWrongQueue = vi.fn(async () => ({
    total: 2,
    items: [
      { id: "w1", plan_id: "p1", node_id: "k1", type: "choice" as const, question: "第一条错题", options: ["对", "错"], answer: "对" },
      { id: "w2", plan_id: "p1", node_id: "k2", type: "choice" as const, question: "第二条错题", options: ["对", "错"], answer: "对" },
    ],
  }));
  port.tutorWrongOverview = vi.fn(async () => ({ total: 2, items: [] }));
  port.tutorQuizSubmit = vi.fn(async () => ({ correct: true }));
  render(<Tutor port={port} view={{ k: "review" }} onGo={() => undefined} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />);

  await user.click(await screen.findByRole("button", { name: "错题" }));
  expect(await screen.findByText("第一条错题")).toBeTruthy();
  await user.click(screen.getByRole("radio", { name: "1 对" }));
  await user.click(screen.getByRole("button", { name: "判定" }));
  expect(await screen.findByText(/答对了/)).toBeTruthy();
  // The advance beat hands the second wrong item — not the first one again.
  await screen.findByText("第二条错题", {}, { timeout: 3000 });
  expect(screen.queryByText("第一条错题")).toBeNull();
});

// The KB's generate button seeds a session the server never starts; the run
// begins when the pane opens the ordinary generate flow, so the page lands
// on live progress rather than on a session with no record.
it("starts the seeded KB generation and lands on its progress", async () => {
  const user = userEvent.setup();
  const { go } = mounted();
  go({ k: "kb" });
  await user.click(await screen.findByRole("button", { name: "添加文本" }));
  await user.type(await screen.findByPlaceholderText("标题（可空，取正文首行）"), "Rust 手册");
  await user.type(screen.getByPlaceholderText("粘贴文本内容"), "所有权是 Rust 的核心概念。");
  await user.click(screen.getByRole("button", { name: "入库" }));
  const box = await screen.findByRole("checkbox", { name: /选择 Rust 手册/ });
  await user.click(box);
  await user.click(screen.getByRole("button", { name: /用所选生成方案/ }));
  expect(await screen.findByText("正在生成你的学习方案")).toBeTruthy();
});

// A degraded run delivers a plan whose lists never landed — the kernel's nil
// slices arrive as JSON null. The detail page says "none of those" instead of
// taking the whole surface down.
it("renders a degraded plan whose node, schedule, and topic lists are null", async () => {
  const user = userEvent.setup();
  const port = new MockPort() as unknown as AgentPort;
  const plan = {
    id: "p1", session_id: "s1", version: 1, title: "", topic: "",
    created_at: "2026-10-16T00:00:00Z", updated_at: "2026-10-16T00:00:00Z",
    intent: { topic: "", level: "未明确", goal: "系统学习并掌握 该主题", daily_minutes: 60 },
    knowledge_map: { nodes: null },
    stages: [
      { index: 0, name: "入门", goal: "建立基础", difficulty: 1, estimated_weeks: 1, topics: null, milestone: "理解基础概念" },
    ],
    schedule: null,
    metadata: { model: "mock" },
  };
  port.tutorPlan = vi.fn(async () => plan as never);
  port.tutorPlanVersions = vi.fn(async () => []);
  render(<Tutor port={port} view={{ k: "plan", pid: "p1" }} onGo={() => undefined} onChat={() => undefined} onSettings={() => undefined} onError={() => undefined} />);
  expect(await screen.findByText("0 个知识点")).toBeTruthy();
  expect(screen.getByText(/入门/)).toBeTruthy();
  // The schedule tab renders its empty table rather than a blank surface.
  await user.click(screen.getByRole("tab", { name: "时间线" }));
  expect(screen.getByRole("table")).toBeTruthy();
});
