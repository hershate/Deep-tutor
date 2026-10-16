import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "../i18n";
import type { Adder } from "./addws";
import { useDismiss } from "./dismiss";
import { pinToViewport } from "./place";
import { StudioIcon } from "./StudioIcon";
import { workspaceMenuKeys } from "./WorkspaceOrder";

export function LocalMachine({ shut, count, adder, canSelect, selecting, onToggle, onSelect }: {
  shut: boolean;
  count?: number;
  adder: Adder;
  canSelect: boolean;
  selecting: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const more = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const close = () => { setOpen(false); more.current?.focus(); };
  useDismiss(open, more, close, menu);
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [open]);
  useLayoutEffect(() => {
    const anchor = more.current?.getBoundingClientRect();
    if (open && anchor && menu.current) pinToViewport(menu.current, anchor.right + 8, anchor.top - 7, 12);
  }, [open]);

  return (
    <div className="machrow" data-here="" role="treeitem" aria-expanded={!shut}
      data-action-click="tree.navigate" onClick={(ev) => {
        if ((ev.target as HTMLElement).closest("button:not(.twist)")) return;
        onToggle();
      }}>
      <button className="twist" tabIndex={-1} aria-hidden="true">
        <svg viewBox="0 0 10 10"><path d="M3.4 1.6 6.8 5 3.4 8.4" /></svg>
      </button>
      <i className="rmtpip" aria-hidden="true" />
      <span className="rmtname">{t("这台机器")}</span>
      <span className="rowswap">
        {count !== undefined && <span className="rowcount" data-words="">{t("{n} 个项目", { n: count })}</span>}
        <span className="rowbtns">
          <button className="machpick" data-busy={adder.busy ? "" : undefined}
            title={t("打开或新建项目…")} aria-label={t("打开或新建项目…")}
            data-action="workspace.add" onClick={() => adder.add()}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.7v8.6M3.7 8h8.6" /></svg>
          </button>
          <button ref={more} className="wsmore" title={t("更多操作")}
            aria-label={t("这台机器的更多操作")} aria-haspopup="menu" aria-expanded={open}
            data-action="machine.menu" onClick={() => setOpen((v) => !v)}>
            <StudioIcon name="more" />
          </button>
        </span>
      </span>
      {open && createPortal(
        <div ref={menu} className="session-pop" role="menu" aria-label={t("这台机器的更多操作")}
          data-action-keydown="machine.menu" onKeyDown={workspaceMenuKeys}>
          <div className="session-pop-head"><b>{t("这台机器")}</b></div>
          <div className="session-pop-group">
            <button role="menuitem" data-action="session.selection" data-value="start"
              disabled={!canSelect || selecting} onClick={() => { close(); onSelect(); }}>
              <StudioIcon name="check" /><span>{t("选择本机会话")}</span>
            </button>
          </div>
        </div>, document.body,
      )}
    </div>
  );
}
