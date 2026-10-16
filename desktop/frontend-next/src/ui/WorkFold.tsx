import type { ReactNode } from "react";
import { t } from "../i18n";
import { StudioIcon } from "./StudioIcon";

/** One turn's intermediate work under a single line. The body is always
 *  rendered, closed or not: find, copy and the graph's landing reach into it,
 *  and opening it must not remount what was already drawn. */
export function WorkFold({ open, onOpen, calls, failures, words, children }: {
  open: boolean;
  onOpen: (next: boolean) => void;
  calls: number;
  failures: number;
  words: number;
  children: ReactNode;
}) {
  return (
    <details className="activity-group work-fold" data-failed={failures ? "" : undefined} open={open} onToggle={(e) => e.currentTarget.open !== open && onOpen(e.currentTarget.open)}>
      <summary>
        <StudioIcon name={failures ? "warning" : "check"} className="activity-status-icon" />
        <span className="activity-title">{t("中间过程")}</span>
        {failures > 0 && <span className="activity-errors">{t("{n} 项失败", { n: failures })}</span>}
        <span className="activity-count">{calls > 0 ? t("{count} 项操作", { count: calls }) : t("思考与中间文字")}</span>
        {words > 0 && calls > 0 && <span className="activity-count">{t("文字 {count}", { count: words })}</span>}
        <StudioIcon name="down" className="activity-fold-icon" />
      </summary>
      <div className="activity-body work-fold-body">{children}</div>
    </details>
  );
}
