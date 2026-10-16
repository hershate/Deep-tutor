import { expect, it, vi } from "vitest";
import { HttpError } from "../port/http_error";
import type { AgentPort, BrowserTab } from "../port/port";
import { failedPageID, observePageTabs, openPage, pageForEmptyColumn, pageInFlight, preparePage, readPageTabs, recheckPageTabs, pageReadAfterSettlement, unseenPageOpenings } from "./browser_open";

it.each(["browser.open_failed", "browser.engine_missing", "browser.engine_failed", "browser.profile_busy"])("reads the public tab identity from %s", (code) => {
  expect(failedPageID(new HttpError(400, "failed", { code, params: { tab: "b1" } }))).toBe("b1");
});

it.each([
  new Error("b1"), { reason: { code: "browser.open_failed", params: { tab: "b1" } } },
  new HttpError(400, "failed"),
  new HttpError(400, "failed", { code: "unrelated.failure", params: { tab: "b1" } }),
  ...[undefined, "", "   ", 1].map((tab) => new HttpError(400, "failed", { code: "browser.open_failed", params: tab === undefined ? {} : { tab } })),
])("does not infer an opening identity from %j", (error) => {
  expect(failedPageID(error)).toBeUndefined();
});

it("reserves preparation before it runs and releases a failure that created no tab", async () => {
  const port = { browserOpen: vi.fn() } as unknown as AgentPort;
  const prepare = vi.fn(async () => { throw new Error("no workspace"); });
  const pending = preparePage(port, prepare);
  const failure = expect(pending).rejects.toThrow("no workspace");
  expect(unseenPageOpenings(port)).toEqual([pending]);
  const inFlight = pageInFlight(port);
  void inFlight?.catch(() => {});
  expect(inFlight).toBeNull();
  expect(prepare).not.toHaveBeenCalled();
  await failure;
  expect(port.browserOpen).not.toHaveBeenCalled();
  expect(unseenPageOpenings(port)).toHaveLength(0);
});

it("does not make an explicit browser wait for an unstarted preview preparation", async () => {
  const tab: BrowserTab = {id:"blank",target:"blank-target",url:"about:blank",title:"Blank",active:false};
  const port = { browserOpen: vi.fn(async () => tab) } as unknown as AgentPort;
  let ready!: (tab: BrowserTab) => void;
  const prepared = preparePage(port, () => new Promise((resolve) => {ready=resolve;}));
  const browser = pageForEmptyColumn(port);
  expect(port.browserOpen).toHaveBeenCalledWith("about:blank", true);
  expect(await browser).toBe(tab);
  ready({...tab,id:"preview",target:"preview-target"});
  await prepared;
  expect(port.browserOpen).toHaveBeenCalledTimes(1);
});

it("still shares an actual browser request when a later preview is only preparing", async () => {
  const tab: BrowserTab = {id:"native",target:"native-target",url:"about:blank",title:"Native",active:false};
  let done!: (tab: BrowserTab) => void, ready!: (tab: BrowserTab) => void;
  const port = { browserOpen: vi.fn(() => new Promise<BrowserTab>((resolve) => {done=resolve;})) } as unknown as AgentPort;
  const native = openPage(port, "about:blank");
  const prepared = preparePage(port, () => new Promise((resolve) => {ready=resolve;}));
  expect(pageInFlight(port)).not.toBeNull();
  const shared = pageForEmptyColumn(port);
  expect(port.browserOpen).toHaveBeenCalledTimes(1);
  done(tab);
  expect(await shared).toBe(tab);
  await native;
  ready({...tab,id:"existing",target:"existing-target"});
  await prepared;
});

it("does not redirect a newer browser request to an older preparation that starts late", async () => {
  const native: BrowserTab = {id:"native",target:"native-target",url:"about:blank",title:"Native",active:false};
  const preview = {...native,id:"preview",target:"preview-target",url:"file:///w/index.html"};
  let ready!: (url: string) => void, done!: (tab: BrowserTab) => void;
  const port = { browserOpen: vi.fn((url: string) => url === "about:blank"
    ? new Promise<BrowserTab>((resolve) => {done=resolve;}) : Promise.resolve(preview)) } as unknown as AgentPort;
  const prepared = preparePage(port, () => new Promise((resolve) => {ready=resolve;}));
  const requested = pageForEmptyColumn(port);
  await Promise.resolve();
  ready(preview.url);
  await prepared;
  done(native);
  expect(await requested).toBe(native);
});

it.each([false, true])("keeps settled navigation ownership until its URL is observed, or its early tab closes: %s", async (closed) => {
  const tab: BrowserTab = {id:"page",target:"target",url:"https://final.example/",title:"Final",active:true};
  let done!: (tab: BrowserTab) => void;
  const port = {browserOpen:vi.fn(() => new Promise<BrowserTab>(resolve => {done=resolve;}))} as unknown as AgentPort;
  const other = {} as AgentPort;
  const pending = openPage(port, "https://redirect.example/");
  observePageTabs(port, [{...tab,url:"about:blank"}]);
  observePageTabs(other, [tab]);
  if (closed) observePageTabs(port, []);
  done(tab); await pending;
  expect(unseenPageOpenings(port)).toHaveLength(closed ? 0 : 1);
  observePageTabs(port, [tab]);
  expect(unseenPageOpenings(port)).toHaveLength(0);
});

it.each([false, true])("retires a successful opening from a fresh read even when its final URL was coalesced (closed=%s)", async (closed) => {
  const tab: BrowserTab = {id:"page",target:"target",url:"https://final.example/",title:"Final",active:true};
  let done!: (tab: BrowserTab) => void, oldRead!: (tabs: BrowserTab[]) => void;
  const port = {browserOpen:vi.fn(() => new Promise<BrowserTab>(resolve => {done=resolve;})), browserTabs:vi.fn(() => new Promise<BrowserTab[]>(resolve => {oldRead=resolve;}))} as unknown as AgentPort;
  const pending = openPage(port, tab.url);
  const stale = readPageTabs(port);
  done(tab); await pending;
  oldRead([{...tab,url:"about:blank"}]);
  observePageTabs(port, await stale);
  expect(unseenPageOpenings(port)).toHaveLength(1);
  vi.mocked(port.browserTabs).mockResolvedValue(closed ? [] : [{...tab,url:"https://later.example/"}]);
  observePageTabs(port, await readPageTabs(port));
  expect(unseenPageOpenings(port)).toHaveLength(0);
});

it("shares concurrent verification reads only across operations already settled at read start", async () => {
  const tab: BrowserTab = {id:"page",target:"target",url:"https://final.example/",title:"Final",active:true};
  const replies: ((tab: BrowserTab) => void)[] = [], reads: ((tabs: BrowserTab[]) => void)[] = [];
  const port = {browserOpen:vi.fn(() => new Promise<BrowserTab>(done => replies.push(done))), browserTabs:vi.fn(() => new Promise<BrowserTab[]>(done => reads.push(done)))} as unknown as AgentPort;
  const first = openPage(port, tab.url), second = openPage(port, tab.url);
  replies[0](tab); await first;
  const early = recheckPageTabs(port, [first]);
  expect(recheckPageTabs(port, [first])).toBe(early);
  await vi.waitFor(() => expect(port.browserTabs).toHaveBeenCalledOnce());
  replies[1]({...tab,id:"other"}); await second;
  const later = recheckPageTabs(port, [first, second]);
  await vi.waitFor(() => expect(port.browserTabs).toHaveBeenCalledTimes(2));
  reads[0]([tab]); reads[1]([tab]);
  expect(pageReadAfterSettlement(port, await early, second)).toBe(false);
  expect(pageReadAfterSettlement(port, await later, second)).toBe(true);
});
