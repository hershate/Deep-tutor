import type { AgentPort } from "../port/port";

// Whether the delivery receipt is drawn as a card is the kernel's setting
// ([ui] show_receipt), so the terminal and every window answer alike. Each
// kernel a pane talks to has its own file, so the last answer is kept per port;
// until a kernel has answered nothing is drawn, and the record itself is kept
// in the session either way.
const LEGACY_RECEIPT_KEY = "rx-turn-receipt";

export type ReceiptState = "unknown" | "on" | "off";

export interface ReceiptPref {
  state(): ReceiptState;
  subscribe(fn: () => void): () => void;
  adopt(state: Exclude<ReceiptState, "unknown">): void;
}

function createReceiptPref(initial: ReceiptState): ReceiptPref {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    state: () => state,
    subscribe: (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    adopt: (next) => {
      if (state === next) return;
      state = next;
      listeners.forEach((fn) => fn());
    },
  };
}

const standalone = createReceiptPref("off");
const perPort = new WeakMap<object, ReceiptPref>();

/** The preference of the kernel behind this port. Without a port — a transcript
 *  drawn outside a pane — it is the standalone one, which starts off. */
export function receiptPrefOf(port?: object): ReceiptPref {
  if (!port) return standalone;
  let pref = perPort.get(port);
  if (!pref) {
    pref = createReceiptPref("unknown");
    perPort.set(port, pref);
  }
  return pref;
}

export const showsReceipt = (port?: object): boolean => receiptPrefOf(port).state() === "on";

type ReceiptPort = Pick<AgentPort, "receiptSettings" | "saveReceiptSettings">;

// Reads the kernel's setting. A choice this browser made before the setting
// moved belongs to this machine, so only this machine's own kernel (`local`)
// takes it over, once: the kernel gets the value only when it is "on" (off is
// its default), and the old key is dropped either way.
export async function loadReceiptPref(port: ReceiptPort, local: boolean): Promise<void> {
  const pref = receiptPrefOf(port);
  let legacy: string | null = null;
  if (local) {
    try {
      legacy = localStorage.getItem(LEGACY_RECEIPT_KEY);
    } catch {
      /* no storage: nothing to carry over */
    }
  }
  try {
    let state = await port.receiptSettings();
    if (legacy === "on" && !state.show) state = await port.saveReceiptSettings(true);
    pref.adopt(state.show ? "on" : "off");
    if (legacy !== null) {
      try {
        localStorage.removeItem(LEGACY_RECEIPT_KEY);
      } catch {
        /* the migration re-runs and finds the kernel already agreeing */
      }
    }
  } catch {
    pref.adopt("off");
  }
}

// Shows at once and asks the kernel to keep it; a refusal puts it back.
export async function saveReceiptPref(port: ReceiptPort, on: boolean): Promise<void> {
  const pref = receiptPrefOf(port);
  const before = pref.state() === "on" ? "on" : "off";
  pref.adopt(on ? "on" : "off");
  try {
    pref.adopt((await port.saveReceiptSettings(on)).show ? "on" : "off");
  } catch (e) {
    pref.adopt(before);
    throw e;
  }
}
