import { useEffect, useSyncExternalStore } from "react";
import { t } from "../../i18n";
import type { AgentPort } from "../../port/port";
import { boot, subscribeTasks, taskSnapshot } from "./engine";
import "./Tutor.css";
import { Home } from "./Home";
import { Generate } from "./Generate";
import { Clarify } from "./Clarify";
import { Plans } from "./Plans";
import { PlanDetail } from "./PlanDetail";
import { Learn } from "./Learn";
import { Review } from "./Review";
import { Notes } from "./Notes";
import { UsageView } from "./UsageView";
import { SearchView } from "./SearchView";
import { Kb } from "./Kb";

// consumeSurfaceHash answers which surface the address asked for — #tutor or
// #chat — and clears it, so a hand-off link opens that surface once and the
// address bar stops carrying state. Terminals and outside links point here.
export function consumeSurfaceHash(): "tutor" | "chat" | null {
  const hash = window.location.hash;
  if (hash !== "#tutor" && hash !== "#chat") return null;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return hash === "#tutor" ? "tutor" : "chat";
}

// The tutor is the app's surface, not a sheet over a conversation: its page
// family is what the window shows, and the agent chat is a page reached from
// the header. There is no URL router here, so the routes become a view union
// the shell switches on — home/generate/clarify/plans/plan/learn/review/
// notes/usage/kb/search.
export type TutorView =
  | { k: "home" }
  | { k: "generate"; sid: string }
  | { k: "clarify"; sid: string }
  | { k: "plans" }
  | { k: "plan"; pid: string; tab?: PlanTab }
  | { k: "learn"; pid?: string }
  | { k: "review" }
  | { k: "notes" }
  | { k: "usage" }
  | { k: "kb"; doc?: string }
  | { k: "search"; q?: string };

export type PlanTab = "outline" | "schedule" | "resources" | "validation" | "materials" | "quiz";

export function useTasks() {
  return useSyncExternalStore(subscribeTasks, taskSnapshot, taskSnapshot);
}

interface Props {
  port: AgentPort;
  view: TutorView;
  onGo: (view: TutorView) => void;
  /** Leaves the tutor for the agent chat, where Reasonix's full surface lives. */
  onChat: () => void;
  /** Opens the app settings — the model the tutor generates with is set there. */
  onSettings: () => void;
  onError: (message: string) => void;
}

export function Tutor({ port, view, onGo, onChat, onSettings, onError }: Props) {
  useEffect(() => {
    void boot(port);
  }, [port]);

  // The crumb answers "where am I" with the page's own name — the same slot
  // the chat pane's crumb answers from; the pane's pages live in the shell's
  // session rail now, not in a second nav drawn inside the pane.
  const crumb = TUTOR_NAV.find((n) => n.key === view.k || (n.key === "plans" && (view.k === "plan" || view.k === "learn")));

  return (
    <div className="tut tut-root" aria-labelledby="tut-title">
      <div className="chrome tut-chrome">
        <div className="crumb">
          <b id="tut-title">{t("学习导师")}</b>
          <span className="crumbsep">/</span>
          <span className="tut-crumb-page">{crumb ? crumb.label() : t("学习目标")}</span>
        </div>
        <div className="r">
          <button
            type="button"
            className="thbtn"
            data-action="tutor.search"
            aria-label={t("搜索")}
            title={t("搜索")}
            onClick={() => onGo({ k: "search" })}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M7.2 3.1a4.1 4.1 0 1 0 0 8.2 4.1 4.1 0 0 0 0-8.2M10.3 10.3 13 13" /></svg>
          </button>
          <button
            type="button"
            className="thbtn"
            data-action="tutor.settings"
            aria-label={t("设置")}
            title={t("设置")}
            onClick={onSettings}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M9 4v6M16 14v6" /></svg>
          </button>
          <button
            type="button"
            className="thbtn"
            data-action="tutor.chat"
            aria-label={t("聊天")}
            title={t("聊天")}
            onClick={onChat}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 4.4a1.2 1.2 0 0 1 1.2-1.2h7.2a1.2 1.2 0 0 1 1.2 1.2v5a1.2 1.2 0 0 1-1.2 1.2H7.2L4.4 13v-2.4a1.2 1.2 0 0 1-1.2-1.2Z" /></svg>
          </button>
        </div>
      </div>
      <div className="tut-body">
        {view.k === "home" && <Home port={port} onGo={onGo} onError={onError} />}
        {view.k === "generate" && <Generate port={port} sid={view.sid} onGo={onGo} onError={onError} />}
        {view.k === "clarify" && <Clarify port={port} sid={view.sid} onGo={onGo} onError={onError} />}
        {view.k === "plans" && <Plans port={port} onGo={onGo} onError={onError} />}
        {view.k === "plan" && <PlanDetail port={port} pid={view.pid} initialTab={view.tab} onGo={onGo} onError={onError} />}
        {view.k === "learn" && <Learn port={port} pid={view.pid} onGo={onGo} onError={onError} />}
        {view.k === "review" && <Review port={port} onGo={onGo} onError={onError} />}
        {view.k === "notes" && <Notes port={port} onError={onError} />}
        {view.k === "usage" && <UsageView port={port} onError={onError} />}
        {view.k === "kb" && <Kb port={port} onGo={onGo} onError={onError} openDoc={view.doc} />}
        {view.k === "search" && <SearchView port={port} onGo={onGo} onError={onError} initialQ={view.q} />}
      </div>
    </div>
  );
}

// The pane's pages, in the order the session rail lists them. The rail owns
// the entries; the pane keeps only the crumb's current-page name.
export const TUTOR_NAV: { key: TutorView["k"]; label: () => string; view: TutorView }[] = [
  { key: "home", label: () => t("学习目标"), view: { k: "home" } },
  { key: "plans", label: () => t("我的方案"), view: { k: "plans" } },
  { key: "review", label: () => t("复习"), view: { k: "review" } },
  { key: "notes", label: () => t("笔记"), view: { k: "notes" } },
  { key: "kb", label: () => t("知识库"), view: { k: "kb" } },
  { key: "usage", label: () => t("用量"), view: { k: "usage" } },
];
