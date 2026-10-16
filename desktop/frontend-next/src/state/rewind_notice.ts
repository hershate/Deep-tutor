import { useSyncExternalStore } from "react";

const PREFIX = "rx-rewind-notice-dismissed:";
const listeners = new Set<() => void>();

function dismissed(transactionId: string | undefined): boolean {
  if (!transactionId) return false;
  try { return localStorage.getItem(PREFIX + transactionId) === "1"; }
  catch { return false; }
}

function refresh(event: StorageEvent) {
  if (event.key === null || event.key.startsWith(PREFIX)) listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  if (listeners.size === 1) window.addEventListener("storage", refresh);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) window.removeEventListener("storage", refresh);
  };
}

export function dismissRewindNotice(transactionId: string): void {
  localStorage.setItem(PREFIX + transactionId, "1");
  listeners.forEach((fn) => fn());
}

export function useRewindNoticeDismissed(transactionId: string | undefined): boolean {
  return useSyncExternalStore(subscribe, () => dismissed(transactionId), () => false);
}
