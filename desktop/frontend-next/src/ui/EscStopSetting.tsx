import { t } from "../i18n";
import { setEscStop, useEscStop } from "../state/escstop";
import { Group } from "./Group";

export function EscStopSetting() {
  const mode = useEscStop();
  return (
    <Group id="esc-stop" title={t("Esc 停止任务")} hint={t("任务运行时按 Esc 的处理方式。点击停止按钮始终立即停止。")}>
      <div className="seg" data-text role="radiogroup" aria-label={t("Esc 停止任务")}>
        <button role="radio" data-action="appearance.esc-stop" data-value="confirm" aria-checked={mode === "confirm"} onClick={() => setEscStop("confirm")}>{t("按两次 Esc 停止")}</button>
        <button role="radio" data-action="appearance.esc-stop" data-value="now" aria-checked={mode === "now"} onClick={() => setEscStop("now")}>{t("按一次 Esc 立即停止")}</button>
      </div>
    </Group>
  );
}
