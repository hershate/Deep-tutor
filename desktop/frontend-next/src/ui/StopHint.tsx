import { t } from "../i18n";

export function StopHint({ armed }: { armed: boolean }) {
  return (
    <div className="stophint" role="status" aria-live="polite" hidden={!armed}>
      {armed ? t("再按一次 Esc 停止当前任务") : ""}
    </div>
  );
}
