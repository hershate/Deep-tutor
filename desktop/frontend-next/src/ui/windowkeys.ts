import { useEffect, useRef } from "react";
import { listenAction } from "./listen";
import { pressedChord, typingElsewhere } from "./keys";

/** One window shortcut, named by the action it performs. `fields` says the
 *  chord is also taken from a text field that did not hand it through. */
export interface Shortcut {
  chord: string;
  shift?: boolean;
  fields?: boolean;
  /** Reads the press itself when a chord and a shift flag cannot say which
   *  keys count, as on layouts that type "+" or "0" from other positions. */
  match?: (e: KeyboardEvent) => boolean;
  action: string;
  run: () => void;
}

/** How long a first Escape stays armed for the second that stops the turn. */
export const STOP_ARM_MS = 2500;

/** The window's keyboard: its shortcut table, and Escape closing the browser
 *  or stopping the turn on screen. With `confirm`, the first Escape only arms
 *  the stop (`onArm` tells the window) and a second within STOP_ARM_MS stops. */
export function useWindowKeys(
  shortcuts: Shortcut[],
  escape: {
    browser: boolean;
    settings: boolean;
    running: boolean;
    confirm: boolean;
    closeBrowser: () => void;
    stop: () => void;
    onArm: (armed: boolean) => void;
  },
) {
  const { browser, settings, running, confirm, closeBrowser, stop, onArm } = escape;
  const armed = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disarm = () => {
    if (armed.current !== null) clearTimeout(armed.current);
    armed.current = null;
    onArm(false);
  };
  useEffect(() => {
    if (!running) disarm();
    return disarm;
  }, [running]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // A control that answered the press itself — the code editor's own find
      // on Ctrl+F, or its Escape closing that — has spent it.
      if (e.defaultPrevented) return;
      const hit = shortcuts.find((s) => (s.match ? s.match(e) : pressedChord(e, s.chord, !!s.shift)));
      if (hit && (hit.fields || !typingElsewhere(e.target))) {
        e.preventDefault();
        hit.run();
      }
      // Escape stops only the turn on screen, and a pane over it closes first.
      // Anything transient takes the press in the capture phase (useDismiss), so
      // one that reaches here is a press nothing else wanted.
      if (e.key === "Escape" && browser) {
        closeBrowser();
      } else if (e.key === "Escape" && !settings && running) {
        if (!confirm || armed.current !== null) {
          disarm();
          stop();
        } else {
          onArm(true);
          armed.current = setTimeout(disarm, STOP_ARM_MS);
        }
      }
    };
    return listenAction(window, "keydown", {
      action: browser ? "browser.open" : "session.stop",
      listener: onKey as EventListener,
    });
  }, [browser, settings, running, confirm, closeBrowser, stop, onArm, shortcuts]);
}
