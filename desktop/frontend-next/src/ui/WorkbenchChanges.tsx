import { useState, type ReactNode } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort, StageRequest, WorkspaceChange } from "../port/port";
import { StudioIcon } from "./StudioIcon";

interface Props {
  port: AgentPort;
  changes: WorkspaceChange[];
  // The path whose diff is on screen, "" when none is.
  current: string;
  onOpen: (path: string) => void;
  // Asked for when staging changed the index. Absent means this surface cannot
  // refresh the list, so it offers no staging either.
  onChanged?: () => void;
  commit: { button: ReactNode; card: ReactNode };
}

const mark = (c: WorkspaceChange): boolean | "mixed" => (c.staged && c.unstaged ? "mixed" : !!c.staged);

// What differs, without opening anything, and which of it the next commit will
// record: the index is what a commit reads, so it is shown and changed here.
export function WorkbenchChanges({ port, changes, current, onOpen, onChanged, commit }: Props) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const settled = changes.length > 0 && changes.every((c) => c.staged && !c.unstaged);

  const run = async (req: StageRequest) => {
    setBusy(true);
    setFailure("");
    try {
      await port.stageChanges(req);
      onChanged?.();
    } catch (e) {
      setFailure(reason(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="workbench-changes">
      <div className="workbench-explorer-head">
        <span>{t("改动")}</span>
        <small>{changes.length}</small>
        {onChanged && (
          <button type="button" className="btn sm workbench-stage-all" data-action="workbench.stage-all" disabled={busy}
            onClick={() => void run({ all: true, unstage: settled })}>
            {t(settled ? "取消全部暂存" : "暂存全部")}
          </button>
        )}
        {onChanged && commit.button}
      </div>
      {commit.card}
      {failure && <p className="refine-error workbench-stage-error" role="alert">{failure}</p>}
      {changes.map((item) => (
        <div className="workbench-change" key={`c:${item.path}`} data-staged={item.staged ? "" : undefined}>
          {onChanged && (
            <button type="button" role="checkbox" className="workbench-stage" data-action="workbench.stage" data-target={item.path}
              aria-checked={mark(item)} aria-label={t("暂存 {path}", { path: item.path })} disabled={busy}
              onClick={() => void run({ paths: [item.path], unstage: item.staged === true && !item.unstaged })}>
              <StudioIcon name="check" aria-hidden="true" />
            </button>
          )}
          <button
            className="workbench-change-row"
            data-action="workbench.diff"
            data-target={item.path}
            title={item.path}
            aria-current={current === item.path ? "page" : undefined}
            onClick={() => onOpen(item.path)}
          >
            <em data-status={item.status}>{item.status || "M"}</em>
            <span>{item.path.slice(item.path.lastIndexOf("/") + 1)}</span>
            {/* Absent counts print nothing: a binary file did not change
                by zero lines, git simply did not count it. */}
            {item.insertions !== undefined && <i data-io="up">+{item.insertions}</i>}
            {item.deletions !== undefined && <i data-io="down">-{item.deletions}</i>}
          </button>
        </div>
      ))}
    </div>
  );
}
