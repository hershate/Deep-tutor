import { useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import { saveChangedFilesPref, useShowsChangedFiles } from "../state/changedfiles";
import { Group } from "./Group";
import { Switch } from "./Switch";

export function ChangedFilesSetting({ port }: { port: AgentPort }) {
  const on = useShowsChangedFiles(port);
  const [error, setError] = useState("");
  return (
    <Group id="changed-files" title={t("文件改动")} hint={t("在 Git 工作区里，输入框上方显示当前有多少文件已更改，点击查看。关闭后，数量回到分支标签旁。")}>
      <div className="lrow">
        <span className="tx">
          <span className="lb">{t("在输入框上方显示文件改动")}</span>
          <span className="ds">{t("默认开启；数量来自 Git 工作区，与本轮对话是否改过文件无关")}</span>
        </span>
        <Switch data-action="chrome.changed-files" on={on} label={t("在输入框上方显示文件改动")} onClick={() => {
          setError("");
          saveChangedFilesPref(port, !on).catch((e) => setError(reason(e)));
        }} />
      </div>
      {error && <div className="why">{error}</div>}
    </Group>
  );
}
