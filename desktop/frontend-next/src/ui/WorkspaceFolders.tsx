import { useEffect, useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort, WorkspaceRoots } from "../port/port";

// Folders this project may write to besides its own, kept in the user's home
// and never in the checkout. Approval mode is a different question: it decides
// whether to ask, not where a write can land, so the two are said apart here.
export function WorkspaceFolders({ port, onChanged }: { port: AgentPort; onChanged: () => void }) {
  const [roots, setRoots] = useState<WorkspaceRoots | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoadFailed(false);
    port.workspaceRoots().then((r) => alive && setRoots(r)).catch(() => alive && setLoadFailed(true));
    return () => { alive = false; };
  }, [port, attempt]);

  if (loadFailed) {
    return (
      <div className="sec" id="set-workspace-folders">
        <h3>{t("此项目的额外文件夹")}</h3>
        <div className="find" data-lvl="warn" role="status">
          <span className="t">{t("无法读取这个项目的额外文件夹")}</span>
          <button className="act" data-action="folders.retry" onClick={() => setAttempt((n) => n + 1)}>{t("重试")}</button>
        </div>
      </div>
    );
  }
  if (!roots) return null;

  const change = async (apply: () => Promise<WorkspaceRoots>) => {
    setBusy(true);
    setError("");
    try {
      setRoots(await apply());
      onChanged();
      return true;
    } catch (e) {
      setError(reason(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const add = async (path: string) => {
    const dir = path.trim();
    if (dir && (await change(() => port.addWorkspaceRoot(dir)))) setDraft("");
  };
  // Choosing only fills the field: granting write access is the Add button's.
  const pick = async () => {
    const dir = await port.pickFolder().catch(() => null);
    if (dir) setDraft(dir);
  };

  return (
    <div className="sec" id="set-workspace-folders">
      <h3>{t("此项目的额外文件夹")}</h3>
      <p className="note">
        {t("agent 默认只能修改当前项目的文件夹。在这里添加的文件夹也允许写入，只对本项目生效，记在你的用户目录里，不会写进仓库；上面的「额外可写目录」则对所有项目生效。")}
      </p>
      <p className="note">
        {t("添加后，agent 将能修改并删除该文件夹里的任何文件。")}
      </p>
      <p className="note">
        {t("「完全放行」只决定是否询问，不会扩大可写范围；要让 agent 修改另一个文件夹，请在这里添加。")}
      </p>
      <div className="extra">
        <div className="sublb">{t("项目文件夹")}</div>
        <div className="kv">
          <span className="k">{t("当前项目")}</span>
          <span className="v"><code>{roots.primary || "—"}</code></span>
        </div>
        {roots.extra.map((p) => (
          <div className="prule" key={p}>
            <code>{p}</code>
            <button
              className="act ghost"
              data-action="folders.remove"
              data-target={p}
              disabled={busy}
              aria-label={t("移除文件夹 {path}", { path: p })}
              onClick={() => void change(() => port.removeWorkspaceRoot(p))}
            >
              {t("移除")}
            </button>
          </div>
        ))}
        <div className="prule" data-add="">
          <input
            value={draft}
            data-action-keydown="folders.add"
            aria-label={t("要添加的文件夹路径")}
            placeholder={t("绝对路径，例如 /Users/you/code/shared-lib")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void add(draft)}
          />
          <button className="act" data-action="folders.pick" disabled={busy} onClick={() => void pick()}>
            {t("选文件夹")}
          </button>
          <button className="act" data-action="folders.add" disabled={busy || !draft.trim()} onClick={() => void add(draft)}>
            {t("添加")}
          </button>
        </div>
      </div>
      {roots.extra.length > 0 && (
        <p className="note">{t("移除后，已经打开的窗格要等重建后才会收窄可写范围。")}</p>
      )}
      {(roots.pending ?? 0) > 0 && (
        <p className="note" role="status">
          {t("还有 {n} 个窗格正在运行，它们会在下次重建时采用这个改动。", { n: roots.pending ?? 0 })}
        </p>
      )}
      {error && (
        <div className="find" data-lvl="err" role="alert">
          <span className="why">{error}</span>
        </div>
      )}
    </div>
  );
}
