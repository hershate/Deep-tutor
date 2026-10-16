import { forwardRef } from "react";
import { createPortal } from "react-dom";
import { t } from "../i18n";
import type { TreeSession } from "../port/hub";
import { StudioIcon } from "./StudioIcon";

export interface SessionMenuProps {
  session: TreeSession;
  label: string;
  /** The conversation behind this row is running a turn. */
  live: boolean;
  pinned: boolean;
  exporting: boolean;
  onPin: () => void;
  onRename: () => void;
  onPause: () => void;
  onClose: () => void;
  onArchive: () => void;
  onCopyInfo: () => void;
  onExport: () => void;
  onDelete: () => void;
}

/** What can be done to one conversation, drawn at the document root so the
 *  sidebar's clipping cannot cut it. Every action closes the menu itself:
 *  the parent's handlers do that, this only says which one was chosen. */
export const SessionMenu = forwardRef<HTMLDivElement, SessionMenuProps>(function SessionMenu(
  { session, label, live, pinned, exporting, onPin, onRename, onPause, onClose, onArchive, onCopyInfo, onExport, onDelete },
  ref,
) {
  const target = session.path;
  return createPortal(
    <div ref={ref} className="session-pop" role="menu" aria-label={t("会话操作")} style={{ maxHeight: "calc(100vh / var(--zoom, 1) - 24px)", overflowY: "auto" }} onClick={(ev) => ev.stopPropagation()}>
      <div className="session-pop-head">
        <b>{label}</b>
        <small>{live ? t("执行中") : t("已完成")} · {t("本地工作区")}</small>
      </div>
      <div className="session-pop-group">
        <button role="menuitem" data-action="session.pin" data-target={target} onClick={onPin}>
          <StudioIcon name="pin" /><span>{pinned ? t("取消置顶") : t("置顶会话")}</span>
        </button>
        <button role="menuitem" data-action="session.rename-start" data-target={target} onClick={onRename}>
          <StudioIcon name="edit" /><span>{t("重命名")}</span>
        </button>
        {session.runtimeId && live && (
          <button role="menuitem" data-action="session.pause" data-target={target} onClick={onPause}>
            <StudioIcon name="pause" /><span>{t("暂停执行")}</span>
          </button>
        )}
        {session.runtimeId && (
          <button
            role="menuitem"
            data-action="session.close"
            data-target={target}
            disabled={live}
            title={live ? t("正在运行，暂停后才能关闭") : undefined}
            onClick={onClose}
          >
            <StudioIcon name="close" /><span>{t("关闭会话")}</span>
          </button>
        )}
        <button
          role="menuitem"
          data-action="session.archive"
          data-target={target}
          data-value={session.archived ? "restore" : "archive"}
          disabled={!!session.runtimeId && live}
          onClick={onArchive}
        >
          <StudioIcon name="archive" /><span>{t(session.archived ? "取消归档" : "归档会话")}</span>
        </button>
      </div>
      <div className="session-pop-divider" />
      <div className="session-pop-group">
        <button role="menuitem" data-action="session.copy-info" data-target={target} onClick={onCopyInfo}>
          <StudioIcon name="copy" /><span>{t("复制会话信息")}</span>
        </button>
        <button role="menuitem" data-action="session.export" data-target={target} disabled={exporting} onClick={onExport}>
          <StudioIcon name="download" /><span>{t("导出会话")}</span><small>JSON</small>
        </button>
      </div>
      <div className="session-pop-divider" />
      <div className="session-pop-group">
        <button className="danger" role="menuitem" data-action="session.delete" data-target={target} onClick={onDelete}>
          <StudioIcon name="trash" /><span>{t("删除会话")}</span>
        </button>
      </div>
    </div>,
    document.body,
  );
});
