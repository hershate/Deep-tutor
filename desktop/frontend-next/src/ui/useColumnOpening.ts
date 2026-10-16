import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { reason } from "../i18n/kernel";
import { host } from "../port/host";
import type { AgentPort } from "../port/port";
import { pageForEmptyColumn, pageInFlight } from "./browser_open";

interface Opening {
  manual: boolean;
  /** Counts the title-bar Browser presses; a change asks for a browser, not just the column. */
  browserAsk: number;
  filesAsk: unknown;
  port: AgentPort;
  agentNow: MutableRefObject<string>;
  dropBlankStart: () => void;
  pick: (key: string) => void;
  follow: (key: string) => void;
  manualSelection: string;
  current: () => () => boolean;
  setFailed: Dispatch<SetStateAction<string>>;
  setBrowsers: Dispatch<SetStateAction<string[]>>;
}

/** What the workbench column does when it is opened. It follows the agent's page,
 *  and a page a followed link already has on its way; with neither it stays on
 *  its launcher until a browser is asked for. The title bar's Browser button
 *  asks for one itself: each press is one ask, and a page already on its way is
 *  shared rather than requested twice. */
export function useColumnOpening({ manual, browserAsk, filesAsk, port, agentNow, dropBlankStart, pick, follow, manualSelection, current, setFailed, setBrowsers }: Opening) {
  const filesRequested = useRef(filesAsk);
  useEffect(() => {
    const files = filesRequested.current !== filesAsk;
    filesRequested.current = filesAsk;
    if (!manual || files) return;
    if (agentNow.current) {
      dropBlankStart();
      follow(`browser:${agentNow.current}`);
      return;
    }
    const valid = current();
    let alive = true;
    pageInFlight(port)
      ?.then((tab) => { if (alive && valid()) follow(`browser:${tab.target}`); })
      .catch((e) => { if (alive && valid()) setFailed(reason(e)); });
    return () => { alive = false; };
  }, [manual, filesAsk, agentNow, dropBlankStart, follow, port, setFailed, current]);

  const asked = useRef(browserAsk);
  useEffect(() => {
    if (asked.current === browserAsk) return;
    asked.current = browserAsk;
    if (!manual) return;
    if (agentNow.current) {
      dropBlankStart();
      pick(`browser:${agentNow.current}`);
      return;
    }
    // A shell that draws views opens a view here too: a framed page is refused
    // by any site sending X-Frame-Options or frame-ancestors, as many do.
    if (host().drawsBrowserViews()) {
      const valid = current();
      let alive = true;
      pageForEmptyColumn(port)
        .then((tab) => { if (alive && valid()) pick(`browser:${tab.target}`); })
        .catch((e) => { if (alive && valid()) setFailed(reason(e)); });
      return () => { alive = false; };
    }
    setBrowsers((open) => (open.length ? open : ["b0"]));
    pick(manualSelection);
  }, [browserAsk, filesAsk, manual, agentNow, dropBlankStart, pick, port, setFailed, setBrowsers, manualSelection, current]);
}
