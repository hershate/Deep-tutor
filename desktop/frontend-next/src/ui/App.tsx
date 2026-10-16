import { watchTreeWake } from "./treewake";
import { usePinnedSessions } from "./usePinnedSessions";
import { AutoArchiveNotice } from "./AutoArchiveNotice";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { reason } from "../i18n/kernel";
import { t } from "../i18n";
import type { AccountState, AgentPort, ProviderSetup } from "../port/port";
import type { HubPort, RuntimeView, TreeWorkspace } from "../port/hub";
import type { TutorPlanSummary } from "../port/tutor";
import { Chrome } from "./Chrome";
import { RailToggle } from "./RailToggle";
import { useLaunchHealth } from "./launchhealth";
import { useTreeWindow, type TreePaging } from "./treewindow";
import { useNavRail } from "./navrail";
import { useLinkRouting } from "./links";
import { clearDraftForSession } from "./drafts";
import { DOCK, RAIL, dockMax, keepWidth, widthOf } from "./Gutter";
import { useWindowKeys, type Shortcut } from "./windowkeys";
import { useEscStop } from "../state/escstop";
import { StopHint } from "./StopHint";
import { folded as roomGaveUp, onRoomWidth } from "./viewport";
import { useFoldAway } from "./foldaway";
import { useDrawerCloses } from "./drawer";
import { RenameSession } from "./RenameSession";
import { RemoteAsk } from "./RemoteAsk";
import { useFeedbackUnread } from "./useFeedbackUnread";
import { Feedback, type FeedbackTab } from "./Feedback";
import { consumeSurfaceHash, type TutorView } from "./tutor/Tutor";
import { Cols } from "./Cols";
import { usePaneForSheets } from "./usePaneForSheets";
import { BrowserLogin } from "./BrowserLogin";
import type { RemoteAsk as RemoteAskT, RemoteHost } from "../port/remote";
import { Boundary } from "./Boundary";
import { SettingsUnavailable } from "./SettingsUnavailable";
import { useMachineBooks } from "./machinebooks";
import { usePaint } from "./paint";
import { useZoomKeys } from "./zoomkeys";
import { TUTOR_PANE, rememberActivePane, savedActivePane } from "./activepane";
import { Sky } from "./Sky";import { useAddWorkspace } from "./addws";
import { AddWorkspacePrompt } from "./AddWorkspacePrompt";
import type { PaneReport } from "./Pane";
import { usePaneTabs } from "./PaneTabs";
import { onShowsSessionTabsChange, showsSessionTabs } from "../state/prefs";
import { useLiveGrace } from "./useLiveGrace";
import { useViewed, useUnreadBadge } from "./unread";
import { useSessionSwitch } from "./sessionswitch";
import { singleFlight } from "./singleflight";
import { useOpenPane } from "./useOpenPane";
import { markSettled } from "../boot/gate";

// Onboarding and welcome play once per machine and never again; their chunks
// ride with the shell anyway — the settings precedent — so a first run draws
// them as soon as the kernel has answered, and every later window parses
// neither. The fallback is the idle frame the gates already draw.
const onboardingModule = import("./Onboarding").then(
  (module) => ({ module, error: null as unknown }),
  (error: unknown) => ({ module: null, error }),
);
const Onboarding = lazy(async () => {
  const loaded = await onboardingModule;
  if (loaded.error) throw loaded.error;
  return { default: loaded.module!.Onboarding };
});
const welcomeModule = import("./Welcome").then(
  (module) => ({ module, error: null as unknown }),
  (error: unknown) => ({ module: null, error }),
);
const Welcome = lazy(async () => {
  const loaded = await welcomeModule;
  if (loaded.error) throw loaded.error;
  return { default: loaded.module!.Welcome };
});

// Start fetching the settings chunk with the shell instead of waiting for the
// first click. It remains a separate chunk (and keeps its failure boundary),
// but opening settings no longer produces a veil while the module catches up.
const settingsModule = import("./Settings").then(
  (module) => ({ module, error: null as unknown }),
  (error: unknown) => ({ module: null, error }),
);
const Settings = lazy(async () => {
  const loaded = await settingsModule;
  if (loaded.error) throw loaded.error;
  return { default: loaded.module!.Settings };
});

const NO_REPORT: PaneReport = {
  status: null,
  title: "",
  steer: 0,
  run: "idle",
  live: false,
  cost: "",
  contextPercent: null,
  context: null,
  mcp: [],
  wallet: "",
};

// App is the window around the panes, not a session itself: the workspace tree,
// the chrome, the settings sheet and the theme are the window's, while every
// conversation — its transcript, metrics and event stream — lives in the Pane
// that owns it. That split is what lets two sessions run side by side.
export function App({ hub }: { hub: HubPort }) {
  const sessionTabs = useSyncExternalStore(onShowsSessionTabsChange, showsSessionTabs, showsSessionTabs);
  const [runtimes, setRuntimes] = useState<RuntimeView[]>([]);
  const [panesRead, setPanesRead] = useState(false);
  // A #chat address hands the window to a conversation; anything else — the
  // bare address a launch has — opens on the tutor's pinned pane. The tutor is
  // a fixed pane id, not a runtime: it cannot be closed, only left.
  const [active, setActive] = useState<string>(() =>
    consumeSurfaceHash() === "chat" ? savedActivePane() : TUTOR_PANE,
  );
  const [tree, setTree] = useState<TreeWorkspace[]>([]);
  // An empty tree before the first read settles is an unread one, not a
  // machine with no folders; only the kernel's answer can say the latter.
  const [treeRead, setTreeRead] = useState(false);
  // Null until asked, and null again where this kernel refuses remote panes —
  // which is what keeps the section out of a browser rather than drawing a
  // heading over a feature that cannot work there.
  const [remotes, setRemotes] = useState<RemoteHost[] | null>(null);
  // Connects in flight. A link reports "connecting" only once the dial starts,
  // so without this the poll would look at an idle list and stand down exactly
  // when the step list is what the user is waiting on.
  const [opening, setOpening] = useState(0);
  // A connect stopped for a question. One at a time by construction: the link
  // that asked is blocked until it is answered.
  const [ask, setAsk] = useState<RemoteAskT | null>(null);
  // 窄到放不下工作区栏时它是收起的，而不是消失的：栏一旦从 DOM 里拿掉，把手也
  // 跟着没了，剩下的入口只有一个没人知道的快捷键。
  const [rail, setRail] = useState(() => !roomGaveUp("rail"));
  const [pinnedSessions, togglePinnedSession, unpinSession] = usePinnedSessions(hub, tree, treeRead, (e) => setError(reason(e)));
  const [railW, setRailW] = useState(() => widthOf(RAIL));
  const [dockW, setDockW] = useState(() => widthOf(DOCK));
  const [dockLimit, setDockLimit] = useState(() => dockMax(document.body.clientWidth));
  const [report, setReport] = useState<PaneReport>(NO_REPORT);
  const [findPulse, setFindPulse] = useState(0);
  const [filesRequests, setFilesRequests] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState<{ path: string; title: string } | null>(null);
  // false = closed, true = open at its last section, a string = open there.
  const [settings, setSettings] = useState<string | boolean>(false);
  const [feedback, setFeedback] = useState<FeedbackTab | null>(null);
  // The study tutor is a pinned pane the window opens on; which of its views
  // is showing survives pane switches, so leaving for a conversation and back
  // loses no place.
  const [tutorView, setTutorView] = useState<TutorView>({ k: "home" });
  // The tutor's plans, listed in the rail beside chat sessions. Read on the
  // same beats the rail refreshes for: launch, and each return to the pane
  // that writes them — a generation while the pane is away is what the next
  // return is for.
  const [tutorPlans, setTutorPlans] = useState<TutorPlanSummary[]>([]);
  const [feedbackUnread, setFeedbackUnread] = useState(0);
  const [browser, setBrowser] = useState(false);
  const [browserAsk, setBrowserAsk] = useState(0);
  const [setup, setSetup] = useState<ProviderSetup | null | undefined>(undefined);
  // undefined until asked; false means the opening sequence is still owed.
  const [welcomed, setWelcomed] = useState<boolean | undefined>(undefined);
  const [account, setAccount] = useState<AccountState | null>(null);
  const [accountUnread, setAccountUnread] = useState("");
  // Bumped when a pane is rebound to another transcript. It rides the Pane key,
  // so the takeover remounts it: every bit of what is on screen belonged to the
  // conversation it just left.
  const [takeover, setTakeover] = useState<Record<string, number>>({});
  // A pane that rebinds to another transcript starts its press counters over:
  // the remounted pane replays a press that arrived before it existed, and one
  // made against the old transcript is not owed by the new one.
  const prevTakeover = useRef(takeover);
  useEffect(() => {
    for (const id of Object.keys(takeover)) {
      if (takeover[id] === prevTakeover.current[id]) continue;
      setFilesRequests((requests) => {
        if (!(id in requests)) return requests;
        const next = { ...requests };
        delete next[id];
        return next;
      });
    }
    prevTakeover.current = takeover;
  }, [takeover]);
  // Every pane's run state, so a tab can show that the conversation behind it
  // is still working. Read through a ref by the report handler, which must stay
  // stable or each frame would re-render every pane.
  const [runs, setRuns] = useState<Record<string, { run: string; live: boolean }>>({});
  const sw = useSessionSwitch();
  const activeRef = useRef("");
  activeRef.current = active;
  useEffect(() => rememberActivePane(active), [active]);
  const reportsRef = useRef<Record<string, PaneReport>>({});
  const runsRef = useRef(runs);
  runsRef.current = runs;

  const fail = useCallback((e: unknown) => setError(reason(e)), []);
  // Asked at the moment a confirmation opens, never subscribed to: runs moves
  // on every usage round, and handing the sidebar that would rebuild a tree of
  // a few hundred sessions each frame — which is what its memo is there for.
  const liveIds = useCallback((ids: string[]) => ids.filter((id) => runsRef.current[id]?.live), []);

  const { listedIds, noteReport } = useLiveGrace(runsRef, runs);

  const onReport = useCallback((id: string, next: PaneReport) => {
    noteReport(id, next.live);
    setRuns((prev) =>
      prev[id]?.run === next.run && prev[id]?.live === next.live ? prev : { ...prev, [id]: { run: next.run, live: next.live } },
    );
    // Every pane's last report, kept in a ref so a background pane's usage
    // round does not re-render the window — and so switching tabs has
    // something to read. Rendering from state here is the same figure at the
    // cost of a window render per background beat.
    reportsRef.current[id] = next;
    if (id === activeRef.current) setReport(next);
  }, [noteReport]);

  // The window's title, run pill and status belong to the pane in front. They
  // were only ever written when a pane reported, so switching to an idle pane
  // left the title naming the conversation that had last spoken — a pane that
  // has nothing to say never says it again. Freshness follows the switch here
  // rather than waiting for the new pane to happen to report.
  useEffect(() => {
    setReport(reportsRef.current[active] ?? NO_REPORT);
  }, [active]);

  const treeWindow = useTreeWindow();
  const readTree = useMemo(
    () => singleFlight(() =>
      hub
        .tree(treeWindow.current())
        .then(setTree)
        .finally(() => setTreeRead(true))),
    [hub, treeWindow],
  );
  const reloadTree = useCallback(() => readTree().catch(() => {}), [readTree]);
  const paging = useMemo<TreePaging>(
    () => ({
      more: (root) => treeWindow.grow(root, readTree),
      view: (v) => { if (treeWindow.view(v)) void reloadTree(); },
    }),
    [treeWindow, readTree, reloadTree],
  );

  // Held beside this machine's tree, and re-read on the same beat: see
  // useMachineBooks for why that beat is one rather than two.
  const { trees: remoteTrees, reload: reloadRemoteTrees, read: readRemoteTree } = useMachineBooks(hub, remotes);

  // Panes and tree move together: opening a session marks its row live, closing
  // one hands the row back.
  const reloadPanes = useCallback(async () => {
    const list = await hub.runtimes().catch(() => [] as RuntimeView[]);
    // Setup and the welcome are read through a pane's port. With none open
    // there is nothing to ask, and waiting on them would leave the window
    // blank; the next pane to open reads both again.
    if (list.length === 0) {
      setSetup((cur) => (cur === undefined ? null : cur));
      setWelcomed((cur) => cur ?? true);
    }
    setRuntimes(list);
    setPanesRead(true);
    // The tutor pane is always a legal place to stand, and a pane that went
    // away hands the window to what remains — or to the tutor, rather than to
    // a blank. An empty hand keeps its emptiness when there is nothing to
    // hand it to: the empty-conversation state is an answer, not a blank.
    setActive((cur) => {
      if (cur === TUTOR_PANE || list.some((rt) => rt.id === cur)) return cur;
      if (cur === "") return list[0]?.id ?? "";
      return list[0]?.id ?? TUTOR_PANE;
    });
    await reloadTree();
    void reloadRemoteTrees();
  }, [hub, reloadTree, reloadRemoteTrees]);
  const refreshPanes = useMemo(() => singleFlight(reloadPanes), [reloadPanes]);

  useEffect(() => {
    void refreshPanes();
  }, [refreshPanes]);

  useEffect(() => {
    const refresh = () => { void refreshPanes(); };
    const stop = hub.onTreeChanged?.(refresh);
    const stopWake = watchTreeWake(refresh);
    return () => { stop?.(); stopWake(); };
  }, [hub, refreshPanes]);

  const reloadRemotes = useCallback(
    () =>
      hub
        .remoteHosts()
        .then(setRemotes)
        .catch(() => setRemotes(null)),
    [hub],
  );

  useEffect(() => {
    void reloadRemotes();
  }, [reloadRemotes]);

  // One timer per answer, not one that runs regardless: a link mid-connect
  // changes by the second, a settled one only when something breaks, and three
  // idle hosts should cost nothing at all.
  useEffect(() => {
    if (!remotes?.length) return;
    const working = opening > 0 || remotes.some((h) => h.status === "connecting" || h.status === "reconnecting");
    if (!working && remotes.every((h) => h.status === "idle")) return;
    const timer = setTimeout(() => void reloadRemotes(), working ? 400 : 5000);
    return () => clearTimeout(timer);
  }, [remotes, opening, reloadRemotes]);

  useEffect(() => hub.onRemoteAsk(setAsk), [hub]);

  const answerRemote = useCallback(
    (id: string, ok: boolean, text: string) => {
      setAsk(null);
      hub.answerRemote(id, ok, text);
    },
    [hub],
  );

  const openRemotePane = useCallback(
    async (host: string, workspace?: string, sessionPath?: string) => {
      setOpening((n) => n + 1);
      try {
        const view = await hub.openRemote({ host, workspace, sessionPath });
        await reloadPanes();
        sw.cancel();
        setActive(view.id);
      } finally {
        setOpening((n) => n - 1);
        void reloadRemotes();
      }
    },
    [hub, reloadPanes, reloadRemotes, sw.cancel],
  );

  // One port per pane, held across renders — a fresh instance would resubscribe
  // the event stream and drop the frames in between.
  const panePorts = useMemo(() => {
    const map = new Map<string, AgentPort>();
    for (const rt of runtimes) map.set(rt.id, hub.portFor(rt));
    return map;
  }, [hub, runtimes]);
  const activePort = panePorts.get(active) ?? panePorts.values().next().value ?? null;
  const machinePort = useMemo(() => hub.machinePort(), [hub]);
  const tutorPort = useMemo(() => hub.tutorPort(), [hub]); // top-level /tutor routes; the settings base 404s them
  const readTutorPlans = useMemo(
    () => singleFlight(() => tutorPort.tutorPlans().then(setTutorPlans).catch(() => {})),
    [tutorPort],
  );
  useEffect(() => {
    void readTutorPlans();
  }, [readTutorPlans]);
  // Returning to the pane that writes the plans is when a stale row would be
  // read as the newest run; the fetch is cheap and single-flighted.
  useEffect(() => {
    if (active === TUTOR_PANE) void readTutorPlans();
  }, [active, readTutorPlans]);
  const machineRt = runtimes.find((rt) => !rt.host);
  const localRt = runtimes.find((rt) => rt.id === active && !rt.host) ?? machineRt;
  const localWorkspacePort = useMemo(() => hub.machinePort(localRt?.id), [hub, localRt?.id]);

  useLaunchHealth(activePort, setup, welcomed);

  // Asked on open, on focus (at most every 30 s) and every 10 min while a report
  // is unsettled, never otherwise; a failed refresh stays silent and backs off.
  useFeedbackUnread(machineRt ? machinePort : null, machineRt?.id ?? "", feedbackUnread, setFeedbackUnread, feedback !== null);

  useEffect(() => {
    if (!activePort) return;
    let alive = true;
    activePort.providerSetup().then((v) => alive && setSetup(v)).catch(() => alive && setSetup(null));
    // A machine that cannot answer has met the app before as far as we care:
    // the sequence must never be what stands between someone and their session.
    activePort.welcomeSeen().then((v) => alive && setWelcomed(v)).catch(() => alive && setWelcomed(true));
    return () => {
      alive = false;
    };
  }, [activePort]);

  // A kernel that does not carry sign-in refuses this with a code and a
  // sentence. Dropping it left the panel saying "checking…" for the rest of
  // the session about a question that had already been answered.
  const reloadAccount = useCallback(() => {
    activePort
      ?.account()
      .then((a) => {
        setAccount(a);
        setAccountUnread("");
      })
      .catch((e) => {
        setAccount(null);
        setAccountUnread(reason(e));
      });
  }, [activePort]);
  useEffect(reloadAccount, [reloadAccount]);

  useEffect(() => {
    if (setup !== undefined && welcomed !== undefined) markSettled();
  }, [setup, welcomed]);

  const running = report.run === "running";
  const { theme, setTheme, scheme, contrast, setContrast, weight, setWeight, effects, look, onLook, pack, reloadThemes } =
    usePaint(hub, runtimes, running, fail);
  // A pane with no session file has never been written to — the empty one every
  // window opens with. Opening a conversation takes it over instead of parking
  // a blank column next to it.
  // A transcript can be thousands of pixels tall. Capturing it into a View
  // Transition made a simple sidebar click pay for a full-page texture before
  // the active id changed. Selection feedback should be immediate.
  const reloadBooks = useCallback(() => Promise.all([reloadTree(), reloadRemoteTrees()]), [reloadTree, reloadRemoteTrees]);
  const viewed = useViewed({ runtimes, ports: panePorts, active: settings || feedback ? "" : active, tree, remote: remoteTrees, reload: reloadBooks });
  useUnreadBadge(viewed.tree, viewed.remote);
  const focusPane = useCallback(
    (id: string) => {
      sw.cancel();
      setActive(id);
      viewed.view(id);
    },
    [sw.cancel, viewed.view],
  );
  // Settings is the next layer over the whole screen and had entry but no exit: it
  // simply vanished on unmount. A view transition can animate out an element that
  // is absent from the new state, so the tree need not stay mounted.
  const showPrefs = useCallback((sec?: string) => setSettings(sec ?? true), []);
  // The host book is edited in settings and read by the sidebar, and nothing
  // else would tell it a machine was added: an idle host reports no change to
  // poll for, so a folder added there stayed invisible until the next launch.
  const hidePrefs = useCallback(() => {
    setSettings(false);
    void reloadRemotes();
  }, [reloadRemotes]);

  const openFiles = useCallback(() => {
    if (!panePorts.has(active)) {
      setError(t("请先打开一个会话，再查看它的文件。"));
      return;
    }
    hidePrefs();
    if (roomGaveUp("scene")) setRail(false);
    setFilesRequests((requests) => ({ ...requests, [active]: (requests[active] ?? 0) + 1 }));
  }, [active, panePorts, hidePrefs]);

  const openPane = useOpenPane({ hub, runtimes, panePorts, active, reportsRef, runsRef, setRuntimes, setActive, setTakeover, reloadPanes: refreshPanes, sw });

  // A rail row for a study plan goes to the plan, not just the pane: the row
  // names one run, and landing on the tutor's last view would hide the thing
  // the click was about.
  const openTutorPlan = useCallback(
    (id: string) => {
      focusPane(TUTOR_PANE);
      setTutorView({ k: "plan", pid: id });
    },
    [focusPane],
  );

  // Awaitable because deleting a conversation has to close its pane first and
  // then wait: the kernel refuses to erase a transcript its runtime still holds,
  // so firing the close off and deleting in the same breath races the teardown.
  // Batched because the reload behind it walks every session on disk, and a
  // folder's worth of panes must not pay for that once each.
  const closePanes = useCallback(
    async (ids: string[]) => {
      for (const id of ids) await hub.close(id);
      await reloadPanes();
    },
    [hub, reloadPanes],
  );

  const adder = useAddWorkspace(hub, reloadTree, fail);
  // 每个窗口都有一个根 —— 没选过项目时那是它碰巧启动的地方。两者读起来一样，
  // 于是「从哪加项目」这句问题永远问不出口；只有内核说的 remembered 分得开。
  const [claimed, setClaimed] = useState(() => localStorage.getItem("rx-claim") === "off");
  const needsProject = treeRead && !claimed && tree.every((ws) => !ws.remembered);

  useFoldAway("rail", setRail);
  const navRail = useNavRail(rail);
  useDrawerCloses(setRail, active, settings);
  useEffect(() => onRoomWidth((width) => setDockLimit(dockMax(width))), []);
  const shownDockW = Math.min(dockW, dockLimit);

  const onRailW = useCallback((w: number) => { setRailW(w); keepWidth(RAIL, w); }, []);
  const onDockW = useCallback((w: number) => { setDockW(w); keepWidth(DOCK, w); }, []);

  useLinkRouting(activePort, useCallback(() => setBrowser(true), []), fail);

  // Settings cover the conversation, so a find opened under them is one nobody
  // sees: asking for it leaves them.
  const openFind = useCallback(() => {
    setSettings(false);
    setFindPulse((n) => n + 1);
  }, []);

  // The window's shortcuts, named by the action each one performs — the same
  // identity the control on screen carries, because they are the same thing
  // asked for two ways. Written out rather than branched so the census can read
  // the set: a chain of ifs is a set nothing can enumerate.
  const zoomKeys = useZoomKeys(look, onLook);
  const shortcuts: Shortcut[] = useMemo(
    () => [
      { chord: "\\", fields: true, action: "rail.toggle", run: () => setRail((v) => !v) },
      { chord: ",", fields: true, action: "chrome.settings", run: showPrefs },
      { chord: "f", action: "transcript.find", run: openFind },
      ...zoomKeys,
    ],
    [showPrefs, openFind, zoomKeys],
  );
  const closeBrowser = useCallback(() => setBrowser(false), []);
  const stopTurn = useCallback(() => activePort?.cancel(), [activePort]);
  const escMode = useEscStop();
  const [stopArmed, setStopArmed] = useState(false);
  useWindowKeys(shortcuts, { browser, settings: !!settings, running, confirm: escMode === "confirm", closeBrowser, stop: stopTurn, onArm: setStopArmed });

  // A setting changed in the pane is a fact about the session behind it, and
  // the pane is what holds that fact. Without a nudge it keeps polling only
  // while a turn runs — so approving mode, preset and model all changed on disk
  // while the screen went on showing what they were when it opened.
  const [settingsPulse, setSettingsPulse] = useState(0);
  const onSettingsChanged = useCallback(() => {
    // Removing the last model source is a setting too, and it has to land the
    // window back on connecting one rather than on a composer with no model.
    activePort?.providerSetup().then(setSetup).catch(() => {});
    reloadAccount();
    setSettingsPulse((n) => n + 1);
    void reloadPanes();
  }, [activePort, reloadAccount, reloadPanes]);

  // The pane strip's rows, and the label source the sidebar shares with it.
  const tabs = usePaneTabs(tree, runtimes, runs, viewed.tree, viewed.remote);
  // The folder only earns tab space when the panes actually span more than one.
  const manyRoots = useMemo(() => new Set(runtimes.map((rt) => rt.root)).size > 1, [runtimes]);
  const activeRuntime = runtimes.find((rt) => rt.id === active);
  // A failed request floats above the composer of the pane in front, where the
  // eye already is; with no pane on screen it takes the main area's corner.
  const activePaneShown = !!activeRuntime && panePorts.has(activeRuntime.id);
  const errorBar = error ? (
    <div className="errbar" role="alert">
      <span>{error}</span>
      <button onClick={() => setError("")}>{t("知道了")}</button>
    </div>
  ) : null;
  const bar = errorBar ?? <AutoArchiveNotice tree={tree} />;
  const activeWorkspace = tree.find((ws) => ws.root === activeRuntime?.root) ?? tree[0];
  // The folder a new session opens in is the one the switcher above names —
  // the window's, not whichever project happens to sit first in the tree.
  const newSessionRoot = activeWorkspace?.root;

  // The tutor header's chat exit: back to the conversation already open, or a
  // fresh one when none is — the same thing the rail's new-session button does.
  const openChat = useCallback(() => {
    if (runtimes.length > 0) {
      focusPane(runtimes[0]!.id);
      return;
    }
    void openPane({ root: newSessionRoot }).catch(fail);
  }, [runtimes, focusPane, openPane, newSessionRoot, fail]);

  usePaneForSheets({
    settings: !!settings,
    feedback: feedback !== null,
    panesRead,
    panes: runtimes.length,
    root: newSessionRoot,
    openPane,
    refuse: (message) => {
      setSettings(false);
      setFeedback(null);
      setError(message);
    },
    fail: (e) => {
      setSettings(false);
      setFeedback(null);
      fail(e);
    },
    clear: (message) => setError((cur) => (cur === message ? "" : cur)),
  });

  // The tab strip has nowhere to await: closing is the end of the gesture there,
  // so a refusal has to land in the error bar rather than in a caller.
  const dropPanes = useCallback((ids: string[]) => void closePanes(ids).catch(fail), [closePanes, fail]);

  const renameSession = useCallback(
    (path: string, title: string, mode: "inline" | "dialog" = "dialog") => {
      if (!path) return;
      if (mode === "inline") void hub.renameSession(path, title).then(reloadTree).catch(fail);
      else setRenaming({ path, title });
    },
    [hub, reloadTree, fail],
  );

  const archiveSession = useCallback(
    async (path: string, archived: boolean, runtimeId?: string) => {
      if (runtimeId) await closePanes([runtimeId]);
      await hub.archiveSession(path, archived);
      if (archived) clearDraftForSession("", path);
      if (archived) unpinSession(path);
      await reloadTree();
    },
    [closePanes, hub, reloadTree, unpinSession],
  );

  if (setup === undefined || welcomed === undefined) return <div className="app" data-run="idle" />;
  if (setup?.required && activePort) {
    return (
      <Suspense fallback={<div className="app" data-run="idle" />}>
        <Onboarding
          port={activePort}
          setup={setup}
          onDone={() => {
            setSetup(null);
            if (!welcomed) {
              setWelcomed(true);
              void activePort.markWelcomed().catch(() => {});
            }
            void reloadPanes();
          }}
        />
      </Suspense>
    );
  }
  if (!welcomed && activePort) {
    return (
      <Suspense fallback={<div className="app" data-run="idle" />}>
        <Welcome
          variant="short"
          replay
          onDone={() => {
            setWelcomed(true);
            void activePort.markWelcomed().catch(() => {});
          }}
        />
      </Suspense>
    );
  }

  return (
    <div
      className="app"
      data-run={report.run}
      data-surface={active === TUTOR_PANE ? "tutor" : "chat"}
      data-rail={rail ? "on" : "off"}
      data-nav={navRail.shown ? "on" : "off"}
      data-browser={browser ? "on" : "off"}
      data-plan={report.status?.plan ? "on" : "off"}
      data-apv={report.status?.toolApprovalMode ?? "ask"}
      data-prefs={settings ? "" : undefined}
      data-tabs={runtimes.length > 1 ? "" : undefined}
      style={{ "--rail-open": `${railW}px`, "--dock-open": `${shownDockW}px` } as CSSProperties}
    >
      {ask && <RemoteAsk ask={ask} onAnswer={answerRemote} />}
      <BrowserLogin />

      <Chrome
        host={runtimes.find((rt) => rt.id === active)?.host}
        port={activePort}
        status={report.status}
        title={active === TUTOR_PANE ? t("学习导师") : (tabs.find((tab) => tab.rt.id === active)?.title ?? report.title)}
        steer={report.steer}
        onSettings={showPrefs}
        onBrowser={() => {
          if (!browser) setBrowserAsk((n) => n + 1);
          setBrowser((value) => !value);
        }}
        browser={browser}
        account={account}
        theme={scheme}
        onTheme={() => setTheme(scheme === "dark" ? "light" : "dark")}
        onFind={openFind}
        hub={hub} onError={fail}
      />

      {pack?.sky && effects !== "reduced" && <Sky />}

      <Cols
        hub={hub}
        navRail={navRail}
        settings={settings}
        showPrefs={showPrefs}
        hidePrefs={hidePrefs}
        openFiles={openFiles}
        openFind={openFind}
        setRail={setRail}
        setFeedback={setFeedback}
        setClaimed={setClaimed}
        setBrowser={setBrowser}
        feedbackUnread={feedbackUnread}
        newSessionRoot={newSessionRoot}
        openPane={openPane}
        openRemotePane={openRemotePane}
        fail={fail}
        viewed={viewed}
        treeRead={treeRead}
        tree={tree}
        rail={rail}
        runtimes={runtimes}
        runs={runs}
        active={active}
        activeWorkspace={activeWorkspace}
        liveIds={liveIds}
        listedIds={listedIds}
        pinnedSessions={pinnedSessions}
        togglePinnedSession={togglePinnedSession}
        remotes={remotes}
        reloadRemotes={reloadRemotes}
        reloadRemoteTrees={reloadRemoteTrees}
        readRemoteTree={readRemoteTree}
        reloadTree={reloadTree}
        paging={paging}
        adder={adder}
        sw={sw}
        focusPane={focusPane}
        closePanes={closePanes}
        panePorts={panePorts}
        archiveSession={archiveSession}
        renameSession={renameSession}
        account={account}
        accountUnread={accountUnread}
        report={report}
        railW={railW}
        onRailW={onRailW}
        sessionTabs={sessionTabs}
        tabs={tabs}
        manyRoots={manyRoots}
        dropPanes={dropPanes}
        onReport={onReport}
        refreshPanes={refreshPanes}
        settingsPulse={settingsPulse}
        findPulse={findPulse}
        filesRequests={filesRequests}
        bar={bar}
        needsProject={needsProject}
        scheme={scheme}
        shownDockW={shownDockW}
        dockLimit={dockLimit}
        onDockW={onDockW}
        browser={browser}
        browserAsk={browserAsk}
        takeover={takeover}
        activePaneShown={activePaneShown}
        tutorPort={tutorPort}
        tutorView={tutorView}
        onTutorView={setTutorView}
        tutorPlans={tutorPlans}
        onOpenTutorPlan={openTutorPlan}
        onChat={openChat}
        onTutorError={setError}
        tutorNotice={errorBar}
      />

      {adder.pathOpen && <AddWorkspacePrompt busy={adder.busy} onSubmit={adder.addPath} onClose={adder.closePath} />}
      {renaming && (
        <RenameSession session={renaming} hub={hub} onClose={() => setRenaming(null)}
          onSaved={() => { setRenaming(null); void reloadTree().catch(fail); }} />
      )}

      {feedback && activePort && (
        <Feedback port={machinePort} tab={feedback} onClose={() => setFeedback(null)} onError={setError} onUnread={setFeedbackUnread} />
      )}

      {settings && activePort && (
        <Boundary fallback={<SettingsUnavailable onClose={hidePrefs} />}>
        <Suspense fallback={<div className="prefs" aria-busy="true" />}>
          <Settings
            hub={hub}
            onError={fail}
            port={activePort}
            networkPort={machinePort}
            localWorkspacePort={localWorkspacePort}
            networkHost=""
            status={report.status}
            theme={theme}
            onTheme={setTheme}
            contrast={contrast}
            weight={weight}
            onWeight={setWeight}
            look={look}
            onLook={onLook}
            onContrast={setContrast}
            onClose={hidePrefs}
            onChanged={onSettingsChanged}
            onSessionsRecovered={() => void reloadTree()}
            reloadThemes={reloadThemes}
            at={typeof settings === "string" ? settings : undefined}
            account={account}
            accountUnread={accountUnread}
            reloadAccount={reloadAccount}
            workspaceRoot={localRt?.root ?? ""}
          />
        </Suspense>
        </Boundary>
      )}
      {active !== TUTOR_PANE && <RailToggle rail={rail} onRail={() => setRail((v) => !v)} />}
      <StopHint armed={stopArmed} />
    </div>
  );
}
