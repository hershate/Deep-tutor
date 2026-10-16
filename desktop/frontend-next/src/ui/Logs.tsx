import { useEffect, useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import type { StudioLogs } from "../port/version";
import { CopyButton } from "./CopyButton";

// Where to look when something goes wrong: the folder the shell and the kernel
// write to, and any crash file the kernel left after an abnormal exit.
export function Logs({ port }: { port: Pick<AgentPort, "studioLogs"> }) {
  const [logs, setLogs] = useState<StudioLogs | null>(null);
  const [failed, setFailed] = useState("");

  useEffect(() => {
    port.studioLogs().then(setLogs, (e) => setFailed(reason(e)));
  }, [port]);

  if (failed) return <p className="acct-note">{failed}</p>;
  if (!logs) return <p className="acct-note">{t("正在读取…")}</p>;
  return (
    <div className="vers">
      <p className="acct-note">
        <code>{logs.dir}</code>{" "}
        <CopyButton text={logs.dir} iconOnly showFeedback label={t("复制日志目录")} />
      </p>
      {logs.crashes.length === 0 ? (
        <p className="acct-note">{t("没有留下异常退出的记录。")}</p>
      ) : (
        <>
          <p className="acct-note">{t("内核异常退出时留下的记录，位于上面目录里的 crash 文件夹：")}</p>
          {logs.crashes.map((c) => (
            <p className="acct-note" key={c.name}>
              <code>{c.name}</code>{" · "}{new Date(c.at).toLocaleString()}{" · "}{Math.max(1, Math.round(c.bytes / 1024))} KB
            </p>
          ))}
          {logs.truncated && <p className="acct-note">{t("还有更早的记录没有列出，请在上面的文件夹里查看。")}</p>}
        </>
      )}
    </div>
  );
}
