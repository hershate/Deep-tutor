import { useSyncExternalStore } from "react";
import type { AgentPort } from "../port/port";

// Whether a turn's intermediate work is folded is the kernel's setting
// ([ui] fold_turn_work). Each kernel a pane talks to has its own file, so the
// last answer is kept per port. It starts off, which is also the kernel's
// default, so an unreadable answer leaves every step drawn.
export interface FoldWorkPref {
  on(): boolean;
  subscribe(fn: () => void): () => void;
  adopt(on: boolean): void;
}

function createPref(): FoldWorkPref {
  let on = false;
  const listeners = new Set<() => void>();
  return {
    on: () => on,
    subscribe: (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    adopt: (next) => {
      if (on === next) return;
      on = next;
      listeners.forEach((fn) => fn());
    },
  };
}

const perPort = new WeakMap<object, FoldWorkPref>();

export function foldWorkPrefOf(port: object): FoldWorkPref {
  let pref = perPort.get(port);
  if (!pref) perPort.set(port, (pref = createPref()));
  return pref;
}

export function useFoldsTurnWork(port: object): boolean {
  const pref = foldWorkPrefOf(port);
  return useSyncExternalStore(pref.subscribe, pref.on, pref.on);
}

type PrefPort = Pick<AgentPort, "foldWorkSettings" | "saveFoldWorkSettings">;

export async function loadFoldWorkPref(port: PrefPort): Promise<void> {
  try {
    foldWorkPrefOf(port).adopt((await port.foldWorkSettings()).fold);
  } catch {
    /* the default stands */
  }
}

// Shows the choice at once and asks the kernel to keep it; a refusal puts it back.
export async function saveFoldWorkPref(port: PrefPort, on: boolean): Promise<void> {
  const pref = foldWorkPrefOf(port);
  const before = pref.on();
  pref.adopt(on);
  try {
    pref.adopt((await port.saveFoldWorkSettings(on)).fold);
  } catch (e) {
    pref.adopt(before);
    throw e;
  }
}
