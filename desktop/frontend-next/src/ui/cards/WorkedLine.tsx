import { t } from "../../i18n";
import { clock, elapsed } from "../../i18n/format";

export function WorkedLine({ ms, startedAt }: { ms: number; startedAt?: number }) {
  return (
    <div className="rc">
      <div className="rc-ok">
        <span className="rc-t">
          {startedAt ? t("{at} 开始 · 耗时 {t}", { at: clock(startedAt), t: elapsed(ms) }) : t("耗时 {t}", { t: elapsed(ms) })}
        </span>
      </div>
    </div>
  );
}
