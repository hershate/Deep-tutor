import { useSyncExternalStore } from "react";

// Whether Escape stops a running turn at once or asks for a second press. It
// is local to this machine, never read from project settings.
export type EscStop = "confirm" | "now";
const KEY = "rx-esc-stop";
const listeners = new Set<() => void>();
let transient: EscStop = "confirm";
let storageFailed = false;

export function escStop(): EscStop {
  if (storageFailed) return transient;
  try {
    return localStorage.getItem(KEY) === "now" ? "now" : "confirm";
  } catch {
    return transient;
  }
}

export function setEscStop(next: EscStop): void {
  transient = next;
  try {
    localStorage.setItem(KEY, next);
    storageFailed = false;
  } catch {
    storageFailed = true;
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  const storage = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) fn();
  };
  window.addEventListener("storage", storage);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", storage);
  };
}

export function useEscStop(): EscStop {
  return useSyncExternalStore(subscribe, escStop, escStop);
}
