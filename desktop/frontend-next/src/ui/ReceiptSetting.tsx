import { useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import { useShowsReceipt } from "../state/foldpref";
import { receiptPrefOf, saveReceiptPref } from "../state/receiptpref";
import { Group } from "./Group";
import { Switch } from "./Switch";

export function ReceiptSetting({ port }: { port: AgentPort }) {
  const on = useShowsReceipt(receiptPrefOf(port));
  const [error, setError] = useState("");
  return (
    <Group id="receipt" title={t("交付验收卡片")} hint={t("每一轮结束后，主机会在会话末尾列出这一轮改了什么、验证了什么、哪些没有验证。记录始终保存，这里只决定是否显示。")}>
      <div className="lrow">
        <span className="tx">
          <span className="lb">{t("显示交付验收卡片")}</span>
          <span className="ds">{t("默认不显示；打开后，当前会话和已有会话里的卡片都会出现。终端界面读同一项设置，下次启动时生效")}</span>
        </span>
        <Switch data-action="chrome.receipt" on={on} label={t("显示交付验收卡片")} onClick={() => {
          setError("");
          saveReceiptPref(port, !on).catch((e) => setError(reason(e)));
        }} />
      </div>
      {error && <div className="why">{error}</div>}
    </Group>
  );
}
