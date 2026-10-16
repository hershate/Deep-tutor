import { nextId } from "./ids";
import type { Item } from "./session_types";

// The kernel's own figure for how long a turn ran. Nothing on this screen times
// it: a local clock would disagree with a reload. Under half a second it would
// round to nothing worth a line.
export function worked(ms?: number, startedAt?: number): Item[] {
  return ms && ms >= 500 ? [{ t: "worked", id: nextId(), ms, startedAt: startedAt || undefined }] : [];
}
