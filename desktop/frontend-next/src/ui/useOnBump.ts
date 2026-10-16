import { useEffect, useRef } from "react";

/** Runs `fn` when `count` changes after mount. A counter is how a request made
 *  twice stays two requests, and mounting is not one — unless `replay`: the
 *  pane behind a lazy boundary can mount after the window's controls were
 *  clicked, and a count that arrived before the pane existed is the earliest
 *  request of all, not one to drop. */
export function useOnBump(count: number, fn: () => void, replay = false): void {
  const seen = useRef(replay ? 0 : count);
  useEffect(() => {
    if (seen.current === count) return;
    seen.current = count;
    fn();
  }, [count]);
}
