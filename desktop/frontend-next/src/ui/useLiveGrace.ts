import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type MutableRefObject } from "react";
import { liveGraceMinutes, onLiveGraceChange } from "../state/prefs";
import { listedAsLive } from "./sessionScope";

type Runs = Record<string, { run: string; live: boolean }>;

// Which conversations the running list holds: the live ones, and those seen to
// finish within the grace this machine chose (off by default). `listedIds`
// changes identity when the grace changes and at each expiry, which is what
// re-draws a memoised list when a row's time is up; `noteReport` is told every
// pane report, before the run state moves, so a live-to-idle edge is seen.
export function useLiveGrace(runsRef: MutableRefObject<Runs>, runs: Runs) {
  const grace = useSyncExternalStore(onLiveGraceChange, liveGraceMinutes, liveGraceMinutes);
  const finishedAt = useRef<Record<string, number>>({});
  const [expiries, setExpiries] = useState(0);
  const listedIds = useCallback(
    (ids: string[]) => ids.filter((id) => listedAsLive(!!runsRef.current[id]?.live, finishedAt.current[id], Date.now(), grace)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [grace, expiries],
  );
  const noteReport = useCallback((id: string, live: boolean) => {
    if (live) delete finishedAt.current[id];
    else if (runsRef.current[id]?.live) finishedAt.current[id] = Date.now();
  }, [runsRef]);
  useEffect(() => {
    if (grace <= 0) return;
    const now = Date.now();
    const ends = Object.values(finishedAt.current).map((at) => at + grace * 60_000).filter((end) => end > now);
    if (ends.length === 0) return;
    const timer = setTimeout(() => setExpiries((n) => n + 1), Math.min(...ends) - now + 50);
    return () => clearTimeout(timer);
  }, [grace, runs, expiries]);
  return { listedIds, noteReport };
}
