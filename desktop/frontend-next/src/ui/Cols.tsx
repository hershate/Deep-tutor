import { lazy, Suspense, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { Gutter, RAIL } from "./Gutter";
import { Nav } from "./Nav";
import { Sidebar } from "./Sidebar";
import { PaneTabs, type usePaneTabs } from "./PaneTabs";
import type { PaneReport } from "./Pane";
import { TutorSurface } from "./TutorSurface";
import type { TutorView } from "./tutor/Tutor";
import { feedbackEntryTab } from "./feedbackentry";
import { t } from "../i18n";
import { TUTOR_PANE } from "./activepane";
import type { AccountState, AgentPort } from "../port/port";
import type { HubPort, RuntimeView, TreeWorkspace } from "../port/hub";
import type { RemoteHost } from "../port/remote";
import type { TreePaging } from "./treewindow";
import type { useAddWorkspace } from "./addws";
import type { useMachineBooks } from "./machinebooks";
import type { usePaint } from "./paint";
import type { useNavRail } from "./navrail";
import type { useOpenPane } from "./useOpenPane";
import type { usePinnedSessions } from "./usePinnedSessions";
import type { useSessionSwitch } from "./sessionswitch";
import type { useLiveGrace } from "./useLiveGrace";
import type { useViewed } from "./unread";
import type { FeedbackTab } from "./Feedback";
import type { TutorPlanSummary } from "../port/tutor";

// A chat pane's tree — transcript, composer, workbench — is the heaviest thing
// the window can show, and the tutor is what it opens on. The pane's chunk is
// fetched with the shell (the settings precedent), so opening a conversation is
// never the network's turn, while the tutor's first paint parses none of it.
const paneModule = import("./Pane").then(
  (module) => ({ module, error: null as unknown }),
  (error: unknown) => ({ module: null, error }),
);
const ChatPane = lazy(async () => {
  const loaded = await paneModule;
  if (loaded.error) throw loaded.error;
  return { default: loaded.module!.Pane };
});

// The one window: navigation, sessions and panes, with the study tutor as a
// pinned pane among them. App still owns the window — chrome, settings, every
// hook that feeds this tree — and the tutor keeps its own view union; what
// moved is the frame, so the rail, the session column and the pane strip are
// the same controls on every pane instead of two isolated surfaces.
export interface ColsProps {
  hub: HubPort;
  navRail: ReturnType<typeof useNavRail>;
  settings: string | boolean;
  showPrefs: (section?: string) => void;
  hidePrefs: () => void;
  openFiles: () => void;
  openFind: () => void;
  setRail: Dispatch<SetStateAction<boolean>>;
  setFeedback: Dispatch<SetStateAction<FeedbackTab | null>>;
  setClaimed: Dispatch<SetStateAction<boolean>>;
  setBrowser: Dispatch<SetStateAction<boolean>>;
  feedbackUnread: number;
  newSessionRoot: string | undefined;
  openPane: ReturnType<typeof useOpenPane>;
  openRemotePane: (host: string, workspace?: string, sessionPath?: string) => Promise<void>;
  fail: (e: unknown) => void;
  viewed: ReturnType<typeof useViewed>;
  treeRead: boolean;
  tree: TreeWorkspace[];
  rail: boolean;
  runtimes: RuntimeView[];
  runs: Record<string, { run: string; live: boolean }>;
  active: string;
  activeWorkspace: TreeWorkspace | undefined;
  liveIds: (ids: string[]) => string[];
  listedIds: ReturnType<typeof useLiveGrace>["listedIds"];
  pinnedSessions: ReturnType<typeof usePinnedSessions>[0];
  togglePinnedSession: ReturnType<typeof usePinnedSessions>[1];
  remotes: RemoteHost[] | null;
  reloadRemotes: () => Promise<void>;
  reloadRemoteTrees: ReturnType<typeof useMachineBooks>["reload"];
  readRemoteTree: ReturnType<typeof useMachineBooks>["read"];
  reloadTree: () => Promise<void>;
  paging: TreePaging;
  adder: ReturnType<typeof useAddWorkspace>;
  sw: ReturnType<typeof useSessionSwitch>;
  focusPane: (id: string) => void;
  closePanes: (ids: string[]) => Promise<void>;
  panePorts: Map<string, AgentPort>;
  archiveSession: (path: string, archived: boolean, runtimeId?: string) => Promise<void>;
  renameSession: (path: string, title: string, mode?: "inline" | "dialog") => void;
  account: AccountState | null;
  accountUnread: string;
  report: PaneReport;
  railW: number;
  onRailW: (w: number) => void;
  sessionTabs: boolean;
  tabs: ReturnType<typeof usePaneTabs>;
  manyRoots: boolean;
  dropPanes: (ids: string[]) => void;
  onReport: (id: string, next: PaneReport) => void;
  refreshPanes: () => Promise<unknown>;
  settingsPulse: number;
  findPulse: number;
  filesRequests: Record<string, number>;
  bar: ReactNode;
  needsProject: boolean;
  scheme: ReturnType<typeof usePaint>["scheme"];
  shownDockW: number;
  dockLimit: number;
  onDockW: (w: number) => void;
  browser: boolean;
  browserAsk: number;
  takeover: Record<string, number>;
  activePaneShown: boolean;
  // The tutor pane's own state: its baseless port, the view it is on, the
  // plans the rail lists beside chat sessions, and where a refusal lands.
  tutorPort: AgentPort;
  tutorView: TutorView;
  onTutorView: (view: TutorView) => void;
  tutorPlans: TutorPlanSummary[];
  onOpenTutorPlan: (id: string) => void;
  /** Leaves the tutor for a conversation — the header's chat exit. */
  onChat: () => void;
  onTutorError: (message: string) => void;
  tutorNotice: ReactNode;
}

export function Cols(props: ColsProps) {
  const {
    hub, navRail, settings, showPrefs, hidePrefs, openFiles, openFind, setRail, setFeedback,
    setClaimed, setBrowser, feedbackUnread, newSessionRoot, openPane, openRemotePane,
    fail, viewed, treeRead, tree, rail, runtimes, runs, active, activeWorkspace, liveIds,
    listedIds, pinnedSessions, togglePinnedSession, remotes, reloadRemotes, reloadRemoteTrees,
    readRemoteTree, reloadTree, paging, adder, sw, focusPane, closePanes, panePorts,
    archiveSession, renameSession, account, accountUnread, report, railW, onRailW, sessionTabs,
    tabs, manyRoots, dropPanes, onReport, refreshPanes, settingsPulse, findPulse, filesRequests,
    bar, needsProject, scheme, shownDockW, dockLimit, onDockW, browser, browserAsk, takeover,
    activePaneShown, tutorPort, tutorView, onTutorView, tutorPlans, onOpenTutorPlan, onChat,
    onTutorError, tutorNotice,
  } = props;
  const tutorActive = active === TUTOR_PANE;
  return (
      <div className="cols">
        {navRail.drawn && (
          <Nav
            at={settings === false ? null : settings === true ? "" : settings}
            onGo={showPrefs}
            onFiles={openFiles}
            onHome={hidePrefs}
            onRail={() => setRail((v) => !v)}
            onFeedback={() => setFeedback(feedbackEntryTab(feedbackUnread))}
            onTutor={() => focusPane(TUTOR_PANE)}
            onNew={() => {
              hidePrefs();
              void openPane({ root: newSessionRoot }).catch(fail);
            }}
            feedbackUnread={feedbackUnread}
            shown={navRail.shown}
          />
        )}
        <Sidebar
          hub={hub}
          collapsed={!rail}
          navShown={navRail.shown}
          tree={viewed.tree}
          treeRead={treeRead}
          runtimes={runtimes}
          runs={runs}
          active={active}
          activeWorkspace={activeWorkspace}
          liveIds={liveIds}
          listedIds={listedIds}
          pinned={pinnedSessions}
          onPin={togglePinnedSession}
          remotes={remotes}
          remoteTrees={viewed.remote}
          reloadRemotes={reloadRemotes}
          reloadRemoteTrees={reloadRemoteTrees}
          readRemoteTree={readRemoteTree}
          reloadTree={reloadTree}
          paging={paging}
          adder={adder}
          opening={sw.path}
          onOpen={openPane}
          onOpenRemote={openRemotePane}
          onFocusPane={focusPane}
          onClosePanes={closePanes}
          onPause={(runtimeId) => void panePorts.get(runtimeId)?.cancel()}
          onArchive={archiveSession}
          onRename={renameSession}
          onCollapse={() => setRail(false)}
          account={account}
          accountUnread={accountUnread}
          wallet={report.wallet}
          onSettings={showPrefs}
          onFiles={openFiles}
          onFeedback={(tab) => setFeedback(tab ?? "send")}
          onTutor={() => focusPane(TUTOR_PANE)}
          tutorView={tutorView}
          onTutorNav={(v) => {
            focusPane(TUTOR_PANE);
            onTutorView(v);
          }}
          tutorPlans={tutorPlans}
          onOpenTutorPlan={onOpenTutorPlan}
          feedbackUnread={feedbackUnread}
          onFind={openFind}
          onError={fail}
        />

        <div className="main">
          <Gutter
            edge="l"
            span={RAIL}
            width={railW}
            label={t("调整工作区栏宽度")}
            open={rail}
            onWidth={onRailW}
            onOpen={setRail}
          />
          {/* One conversation on screen at a time. Side by side, two panes
              squeezed each other and a glance could not tell which composer
              belonged to which run; the ones behind keep streaming either way.
              The tutor is the pinned first tab — always present, never closed —
              so the strip shows as soon as a conversation joins it. */}
          {sessionTabs && tabs.length > 0 && (
            <PaneTabs
              tabs={tabs}
              active={active}
              showRoot={manyRoots}
              home={{ title: t("学习"), onFocus: () => focusPane(TUTOR_PANE) }}
              onFocus={focusPane}
              onClose={dropPanes}
              onRename={(rt, title, mode) => renameSession(rt.sessionPath ?? "", title, mode)}
            />
          )}

          <div className="panes" aria-busy={sw.pending || undefined}>
            <section
              className="pane-tutor"
              aria-label={t("学习导师")}
              data-off={tutorActive ? undefined : ""}
              aria-hidden={tutorActive ? undefined : true}
              data-active={tutorActive ? "" : undefined}
            >
              <TutorSurface
                port={tutorPort}
                view={tutorView}
                onGo={onTutorView}
                onChat={onChat}
                onSettings={() => showPrefs()}
                onError={onTutorError}
                notice={tutorActive ? tutorNotice : null}
              />
            </section>
            {runtimes.map((rt) => {
              const port = panePorts.get(rt.id);
              return port ? (
                <Suspense
                  key={`${rt.id}:${takeover[rt.id] ?? 0}`}
                  fallback={<div className="pane" data-active={rt.id === active ? "" : undefined} data-off={rt.id === active ? undefined : ""} aria-busy="true" />}
                >
                  <ChatPane
                    rt={rt}
                    port={port}
                    title={tabs.find((tab) => tab.rt.id === rt.id)?.title ?? t("新会话")}
                    active={rt.id === active}
                    sideHost={null}
                    side={false}
                    onFocus={() => focusPane(rt.id)}
                    visible={rt.id === active}
                    onReport={onReport}
                    onTurnDone={viewed.turnDone}
                    // Panes, not just the tree: the first turn gives this pane a
                    // session path, and until /runtimes reports it the pane still
                    // looks blank — the next history row would take it over.
                    onSessionChanged={refreshPanes}
                    pulse={settingsPulse}
                    findPulse={findPulse}
                    filesPulse={filesRequests[rt.id] ?? 0}
                    alert={rt.id === active ? bar : undefined}
                    needsProject={needsProject}
                    onOpenProject={() => {
                      setRail(true);
                      adder.add();
                    }}
                    onKeepHere={() => {
                      localStorage.setItem("rx-claim", "off");
                      setClaimed(true);
                    }}
                    onSettings={(section) => section ? showPrefs(section) : showPrefs()}
                    onOpenSession={(path) => void openPane({ root: rt.root, sessionPath: path }).catch(fail)}
                    theme={scheme}
                    dockW={shownDockW}
                    dockMax={dockLimit}
                    onDockW={onDockW}
                    manualBrowser={rt.id === active && browser}
                    browserAsk={browserAsk}
                    onManualBrowser={setBrowser}
                  />
                </Suspense>
              ) : null;
            })}
            {runtimes.length === 0 && !tutorActive && (
              <div className="panes-empty">
                <span className="mk" aria-hidden="true">
                  ⌘
                </span>
                <p className="t">{t("没有打开的会话")}</p>
                {/* With no folder there is nowhere a session could open, so the
                    one button offered is the step that makes it possible. */}
                {!treeRead ? null : tree.length === 0 ? (
                  <>
                    <p className="h">{t("会话在文件夹里打开，先添加一个")}</p>
                    <button data-action="workspace.add" disabled={adder.busy} onClick={() => adder.add()}>{t("添加文件夹")}</button>
                  </>
                ) : (
                  <>
                    <p className="h">{t("从左栏选择，或在当前文件夹新建")}</p>
                    <button data-action="session.new" onClick={() => void openPane({ root: newSessionRoot }).catch(fail)}>{t("新建会话")}</button>
                  </>
                )}
              </div>
            )}
          </div>

          {!activePaneShown && bar}
        </div>

      </div>
  );
}
