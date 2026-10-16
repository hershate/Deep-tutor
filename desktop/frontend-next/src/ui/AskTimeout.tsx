import { useEffect, useState, type KeyboardEvent } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import type { AskTimeoutSettings } from "../port/boundary";
import { Group } from "./Group";
import { Switch } from "./Switch";

// An unanswered ask question can be skipped after a wait the user chooses. It
// ships off: skipping is the one setting here that lets a run go on without a
// decision someone else was asked for, and the model is told it was a timeout.
export function AskTimeout({ port }: { port: AgentPort }) {
  const [state, setState] = useState<AskTimeoutSettings | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const adopt = (s: AskTimeoutSettings) => {
    setState(s);
    setDraft(String(s.seconds || s.defaultSeconds));
  };

  useEffect(() => {
    port.askTimeout().then(adopt).catch(() => setState(null));
  }, [port]);

  const save = async (seconds: number) => {
    setBusy(true);
    setError("");
    try {
      adopt(await port.saveAskTimeout(seconds));
    } catch (e) {
      setError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  const commit = () => {
    if (!state || state.seconds === 0) return;
    const n = Number(draft);
    if (!Number.isInteger(n) || n < state.minSeconds || n > state.maxSeconds) {
      return setError(t("请输入 {min} 到 {max} 之间的整数。", { min: state.minSeconds, max: state.maxSeconds }));
    }
    if (n !== state.seconds) void save(n);
  };

  const on = !!state && state.seconds > 0;
  const body = !state ? (
    <div className="empty">{t("无法读取提问等待设置。")}</div>
  ) : (
    <div className="box">
      <div className="lrow">
        <span className="tx">
          <span className="lb">{t("提问超时后自动跳过")}</span>
          <span className="ds">{t("默认关闭：提问一直等你回答。开启后，超过下面的时间没有回答，这个问题会被跳过，任务继续；模型会被告知这是超时而不是你的回答，并按自己的假设往下做（全自动或目标模式下尤其如此）。终端界面读同一项设置，用 /ask-timeout 修改。")}</span>
        </span>
        <Switch
          data-action="ask-timeout.enabled"
          on={on}
          busy={busy}
          label={t("提问超时后自动跳过")}
          onClick={() => void save(on ? 0 : state.defaultSeconds)}
        />
      </div>
      {on && (
        <div className="lrow threshold-row">
          <span className="tx">
            <span className="lb">{t("等待时长")}</span>
            <span className="ds">{t("从内核发出问题时开始计时。默认 {n} 秒。", { n: state.defaultSeconds })}</span>
          </span>
          <div className="threshold-control">
            <span className="unit-field">
              <input type="text" inputMode="numeric" aria-label={t("等待时长")} data-action-keydown="ask-timeout.seconds" data-action-change="ask-timeout.seconds"
                value={draft} disabled={busy}
                onChange={(e) => setDraft(e.target.value.replace(/\D/g, ""))}
                onBlur={commit}
                onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
              <i>{t("秒")}</i>
            </span>
          </div>
        </div>
      )}
      {state.invalid && <div className="why">{t("配置文件里的提问等待时长无效（须为 0 或 {min} 到 {max} 之间），现在按“一直等待”处理：{path}", { min: state.minSeconds, max: state.maxSeconds, path: state.path })}</div>}
      {error && <div className="why">{error}</div>}
    </div>
  );

  return <Group id="ask-timeout" title={t("提问等待超时")} hint={t("长任务中途遇到提问时，不必一直停在那里等人。")}>{body}</Group>;
}
