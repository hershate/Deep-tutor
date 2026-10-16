import { useSyncExternalStore } from "react";
import type { AgentPort } from "../port/port";

// Whether the changed-files pill is drawn is the kernel's setting
// ([ui] show_changed_files). Each kernel a pane talks to has its own file, so
// the last answer is kept per port. It starts on, which is also the kernel's
// default, so an unreadable answer leaves the pill where it was.
export interface ChangedFilesPref {
  on(): boolean;
  subscribe(fn: () => void): () => void;
  adopt(on: boolean): void;
}

function createPref(): ChangedFilesPref {
  let on = true;
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

const perPort = new WeakMap<object, ChangedFilesPref>();

export function changedFilesPrefOf(port: object): ChangedFilesPref {
  let pref = perPort.get(port);
  if (!pref) perPort.set(port, (pref = createPref()));
  return pref;
}

export function useShowsChangedFiles(port: object): boolean {
  const pref = changedFilesPrefOf(port);
  return useSyncExternalStore(pref.subscribe, pref.on, pref.on);
}

type PrefPort = Pick<AgentPort, "changedFilesSettings" | "saveChangedFilesSettings">;

export async function loadChangedFilesPref(port: PrefPort): Promise<void> {
  try {
    changedFilesPrefOf(port).adopt((await port.changedFilesSettings()).show);
  } catch {
    /* the default stands */
  }
}

// Shows the choice at once and asks the kernel to keep it; a refusal puts it back.
export async function saveChangedFilesPref(port: PrefPort, on: boolean): Promise<void> {
  const pref = changedFilesPrefOf(port);
  const before = pref.on();
  pref.adopt(on);
  try {
    pref.adopt((await port.saveChangedFilesSettings(on)).show);
  } catch (e) {
    pref.adopt(before);
    throw e;
  }
}
