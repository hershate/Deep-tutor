import { useCallback, useEffect, useRef } from "react";
import type { AgentPort, BrowserTab } from "../port/port";
import { failedPageID, observedPageURLs, pageReadAfterSettlement, recheckPageTabs, reconcilePageFollow, unseenPageOpenings } from "./browser_open";

/** A newer choice supersedes user-opened pages across both their response and
 *  tab-list notification. Unrelated agent pages remain eligible to follow. */
export function useBrowserSelection(port: AgentPort, select: (key: string) => void, tabs: BrowserTab[]) {
  const latestTabs = useRef(tabs);
  latestTabs.current = tabs;
  const revision = useRef(0);
  const ignored = useRef(new Set<Promise<BrowserTab>>());
  const following = useRef({ issued: 0, selected: 0 });
  useEffect(() => {
    ignored.current = new Set();
    return () => { revision.current++; };
  }, [port]);
  const supersede = useCallback(() => {
    revision.current++;
    ignored.current = new Set(unseenPageOpenings(port));
  }, [port]);
  const current = useCallback(() => {
    const at = revision.current;
    return () => at === revision.current;
  }, []);
  const pick = useCallback((key: string) => {
    supersede();
    select(key);
  }, [supersede, select]);
  const follow = useCallback((key: string) => {
    const choiceCurrent = current(), order = ++following.current.issued;
    const valid = () => choiceCurrent() && order >= following.current.selected;
    const apply = () => { if (valid()) { following.current.selected = order; select(key); } };
    // Snapshot before this tab-list batch retires its observed requests. A
    // later agent navigation of the same target is a different operation.
    const pending = unseenPageOpenings(port, { startedOnly: true }).filter((page) => ignored.current.has(page));
    if (!pending.length) { apply(); return; }
    const tab = latestTabs.current.find((tab) => `browser:${tab.target}` === key);
    // A later navigation can precede the open response. Capture only locations
    // observed before this event, not ones that arrive while its promise waits.
    const prior = pending.map((page) => tab ? observedPageURLs(port, page, tab.id) : new Set<string>());
    const fresh = pending.map((page) => pageReadAfterSettlement(port, latestTabs.current, page));
    void Promise.allSettled(pending).then(async (results) => {
      if (!valid()) return;
      const changed = (opened: BrowserTab) => opened.url !== tab?.url || (opened.active === false && tab?.active === true);
      const owns = (result: PromiseSettledResult<BrowserTab>, index: number, confirmed = false) => result.status === "fulfilled"
        ? `browser:${result.value.target}` === key && (!changed(result.value) || (!confirmed && !fresh[index] && (result.value.url === tab?.url || !prior[index].has(result.value.url))))
        : !!tab && failedPageID(result.reason) === tab.id;
      if (!results.some((result, index) => owns(result, index))) { apply(); return; }
      const ambiguous = results.some((result, index) => owns(result, index) && result.status === "fulfilled" && changed(result.value));
      if (!tab || !ambiguous) return;
      // A final-URL read can be canceled before the response arrives. Verify
      // this candidate afresh instead of guessing which navigation it was.
      const next = await recheckPageTabs(port, pending).catch(() => null);
      if (!next) return;
      const confirmed = results.map((result, index) => result.status === "fulfilled" && reconcilePageFollow(port, pending[index], next, tab, result.value));
      if (!valid() || latestTabs.current.find((item) => item.target === tab.target)?.url !== tab.url) return;
      if (next.some((item) => item.target === tab.target && item.url === tab.url) && !results.some((result, index) => owns(result, index, confirmed[index]))) apply();
    });
  }, [port, current, select]);
  return { pick, follow, supersede, current };
}
