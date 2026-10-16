import { HttpError, type AgentPort, type BrowserTab } from "../port/port";

interface Opening { page: Promise<BrowserTab>; pending: boolean; started: boolean; order: number; result?: { id: string; url?: string }; seen: Map<string, Set<string>> }
const opening = new WeakMap<AgentPort, Opening>();
const sequence = new WeakMap<AgentPort, number>();
const unseen = new WeakMap<AgentPort, Set<Opening>>();
interface Observation { locations: Map<string, string>; reconciled: boolean }
const observed = new WeakMap<AgentPort, Observation>();
const reads = new WeakMap<BrowserTab[], { port: AgentPort; settled: Set<Promise<BrowserTab>> }>();
const rechecks = new WeakMap<AgentPort, { pages: Set<Promise<BrowserTab>>; read: Promise<BrowserTab[]> }>();

/** Arrival order cannot date a tab snapshot: an older read may finish after
 *  navigation. Keep the response boundary from when its read began. */
export async function readPageTabs(port: AgentPort): Promise<BrowserTab[]> {
  const settled = new Set([...(unseen.get(port) ?? [])].filter((request) => !request.pending).map((request) => request.page));
  const tabs = [...await port.browserTabs()];
  reads.set(tabs, { port, settled });
  return tabs;
}

export function pageReadAfterSettlement(port: AgentPort, tabs: BrowserTab[], page: Promise<BrowserTab>): boolean {
  const read = reads.get(tabs);
  return read?.port === port && read.settled.has(page);
}

export function recheckPageTabs(port: AgentPort, pages: Promise<BrowserTab>[]): Promise<BrowserTab[]> {
  const current = rechecks.get(port);
  if (current && pages.every((page) => current.pages.has(page))) return current.read;
  const next = { pages: new Set(pages), read: Promise.allSettled(pages).then(async () => {
    const tabs = await readPageTabs(port);
    // A matching URL may retire ownership before a pending follower verifies
    // activation. These completed responses still establish read freshness.
    for (const page of pages) reads.get(tabs)!.settled.add(page);
    return tabs;
  }) };
  rechecks.set(port, next);
  const clear = () => { if (rechecks.get(port) === next) rechecks.delete(port); };
  void next.read.then(clear, clear);
  return next.read;
}

/** Confirm just this follower's operation; other tabs still need their own
 *  visible reconciliation before their ownership can be retired. */
export function reconcilePageFollow(port: AgentPort, page: Promise<BrowserTab>, tabs: BrowserTab[], shown: BrowserTab, result: BrowserTab): boolean {
  const requests = unseen.get(port);
  const request = [...(requests ?? [])].find((request) => request.page === page);
  if (result.id !== shown.id || !pageReadAfterSettlement(port, tabs, page)) return false;
  const current = tabs.find((tab) => tab.id === shown.id);
  const confirmed = current?.url === shown.url && (current.url !== result.url || (result.active === false && current.active && shown.active));
  if (request && (!current || confirmed)) requests?.delete(request);
  return confirmed;
}

export function unseenPageOpenings(port: AgentPort, { startedOnly = false } = {}): Promise<BrowserTab>[] {
  return [...(unseen.get(port) ?? [])].filter((request) => !startedOnly || request.started).map((request) => request.page);
}

/** A refused navigation can still leave a tab. The wire names its public ID,
 *  not its browser target; unrelated errors carry no ownership here. */
export function failedPageID(error: unknown): string | undefined {
  if (!(error instanceof HttpError)) return;
  const { code, params } = error.reason ?? {};
  const id = params?.tab;
  return code && ["browser.open_failed", "browser.engine_missing", "browser.engine_failed", "browser.profile_busy"].includes(code)
    && typeof id === "string" && id.trim() ? id : undefined;
}

export function observedPageURLs(port: AgentPort, page: Promise<BrowserTab>, tabID: string): ReadonlySet<string> {
  const request = [...(unseen.get(port) ?? [])].find((opening) => opening.page === page);
  return new Set(request?.seen.get(tabID));
}

function reconciledOpening(request: Opening, observation?: Observation): boolean {
  const result = request.result;
  if (!observation?.reconciled || !result || !request.seen.has(result.id)) return false;
  // An early blank tab is not the completed navigation. A tab already closed
  // needs no future location event, even if its final URL was never observed.
  return result.url === undefined || request.seen.get(result.id)!.has(result.url) || !observation.locations.has(result.id);
}

export function observePageTabs(port: AgentPort, tabs: BrowserTab[], reconciled = true): void {
  const observation = { locations: new Map(tabs.map((tab) => [tab.id, tab.url])), reconciled };
  observed.set(port, observation);
  for (const request of unseen.get(port) ?? []) {
    for (const [id, url] of observation.locations) {
      const urls = request.seen.get(id) ?? new Set<string>();
      urls.add(url);
      request.seen.set(id, urls);
    }
    // A fresh read can pass the returned URL when another navigation replaced
    // its canceled list read. Only success supplies that completion boundary.
    const fresh = request.result?.url !== undefined && pageReadAfterSettlement(port, tabs, request.page);
    if ((reconciled && fresh) || reconciledOpening(request, observation)) unseen.get(port)?.delete(request);
  }
}

/** The one way this window asks the kernel for a tab. What is being opened is
 *  remembered until the kernel answers, so a column that needs a page to show
 *  takes that one rather than asking for another. */
export function openPage(port: AgentPort, url: string): Promise<BrowserTab> {
  return trackPage(port, port.browserOpen(url, true), true).page;
}

/** Reserve the whole user operation before workspace lookup or other awaits.
 *  Preparation may find an existing page instead of creating one. */
export function preparePage(port: AgentPort, prepare: () => Promise<string | BrowserTab>): Promise<BrowserTab> {
  const page = Promise.resolve().then(prepare).then((ready) => {
    if (typeof ready !== "string") return ready;
    markStarted(port, state);
    return port.browserOpen(ready, true);
  });
  const state = trackPage(port, page, false);
  return page;
}

function trackPage(port: AgentPort, page: Promise<BrowserTab>, started: boolean): Opening {
  const order = (sequence.get(port) ?? 0) + 1;
  sequence.set(port, order);
  const state: Opening = { page, pending: true, started, order, seen: new Map([...(observed.get(port)?.locations ?? [])].map(([id, url]) => [id, new Set([url])])) };
  if (started) markStarted(port, state);
  const requests = unseen.get(port) ?? new Set<Opening>();
  unseen.set(port, requests);
  requests.add(state);
  const settled = (result?: Opening["result"]) => {
    state.pending = false;
    state.result = result;
    if (!result || reconciledOpening(state, observed.get(port))) requests.delete(state);
  };
  page.then((tab) => settled({ id: tab.id, url: tab.url }), (error) => {
    const id = failedPageID(error);
    settled(id ? { id } : undefined);
  });
  return state;
}

function markStarted(port: AgentPort, state: Opening): void {
  state.started = true;
  // A slow preparation keeps its original intent order when it reaches the kernel.
  if ((opening.get(port)?.order ?? 0) < state.order) opening.set(port, state);
}

/** The page already on its way for this window, if one is: what a followed link
 *  asked for, which the column opened for it has to show. */
export function pageInFlight(port: AgentPort): Promise<BrowserTab> | null {
  const current = opening.get(port);
  return current?.pending && current.started ? newestPage(port, current.page) : null;
}

/** A page for a column that has none: the one already on its way, else a blank. */
export function pageForEmptyColumn(port: AgentPort): Promise<BrowserTab> {
  const current = opening.get(port);
  return newestPage(port, current?.pending && current.started ? current.page : openPage(port, "about:blank"));
}

async function newestPage(port: AgentPort, page: Promise<BrowserTab>): Promise<BrowserTab> {
  try {
    const tab = await page;
    const next = opening.get(port);
    return next?.started && next.page !== page ? newestPage(port, next.page) : tab;
  } catch (error) {
    const next = opening.get(port);
    if (next?.started && next.page !== page) return newestPage(port, next.page);
    throw error;
  }
}
