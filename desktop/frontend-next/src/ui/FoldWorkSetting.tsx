import { useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import { saveFoldWorkPref, useFoldsTurnWork } from "../state/foldwork";
import { Group } from "./Group";
import { Switch } from "./Switch";

export function FoldWorkSetting({ port }: { port: AgentPort }) {
  const on = useFoldsTurnWork(port);
  const [error, setError] = useState("");
  return (
    <Group id="fold-work" title={t("折叠中间过程")} hint={t("一轮结束后，把工具调用、思考和中间的文字收成一行，只留最后一句话；点那一行可以展开。运行中的一轮始终展开。")}>
      <div className="lrow">
        <span className="tx">
          <span className="lb">{t("折叠一轮里的中间过程")}</span>
          <span className="ds">{t("默认关闭；出错的步骤会在那一行上标出来")}</span>
        </span>
        <Switch data-action="chrome.fold-work" on={on} label={t("折叠一轮里的中间过程")} onClick={() => {
          setError("");
          saveFoldWorkPref(port, !on).catch((e) => setError(reason(e)));
        }} />
      </div>
      {error && <div className="why">{error}</div>}
    </Group>
  );
}
