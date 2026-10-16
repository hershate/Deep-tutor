import { useCallback, useEffect, useState } from "react";
import type { AgentPort, NotifyPrefs } from "../port/port";
import { t } from "../i18n";
import { ApplyNote } from "./Group";
import { Switch } from "./Switch";

/** What this machine is allowed to interrupt for. A kernel with no window
 *  answers nothing and this draws nothing: a notification fired there would
 *  land on the server's desktop, not on the watcher's. */
export function Notifications({ port }: { port: AgentPort }) {
  const [prefs, setPrefs] = useState<NotifyPrefs | null>(null);

  useEffect(() => {
    let live = true;
    port.notifyPrefs().then((p) => live && setPrefs(p)).catch(() => live && setPrefs(null));
    return () => {
      live = false;
    };
  }, [port]);

  // The window answers with what is true afterwards, not with what was asked:
  // the same read is what a second window would get.
  const flip = useCallback(
    (patch: Partial<NotifyPrefs>) => {
      if (!prefs) return;
      port.setNotifyPrefs({ ...prefs, ...patch }).then((got) => got && setPrefs(got)).catch(() => {});
    },
    [port, prefs],
  );

  // A settings page must not grow a heading for a switch it cannot show.
  if (!prefs) return null;
  // The occasions below are for either switch: a person may want the chime and no toast.
  const active = prefs.enabled || prefs.sound;
  return (
    <section className="grp" id="set-notify" data-setting="notify">
      <div className="grp-hd">
        <h3>{t("通知")}</h3>
      </div>
      <p className="hint">{t("一轮结束、等待批准、模型提问或反馈收到回复时，可以发送系统通知。完成后未读的会话会在应用图标上显示标记，查看对应会话后清除。")}</p>
      <div className="grp-items">
        <div className="lrow">
          <span className="tx">
            <span className="lb">{t("发送系统通知")}</span>
            <span className="ds">{t("默认关闭。开启后，无论窗口是否在前台都会由系统弹出提醒；声音由系统决定。立即生效，正在跑的会话下一次事件就按新设置来")}</span>
          </span>
          <Switch data-action="notify.enabled" on={prefs.enabled} label={t("发送系统通知")} onClick={() => flip({ enabled: !prefs.enabled })} />
        </div>
        <div className="lrow">
          <span className="tx">
            <span className="lb">{t("播放提示音")}</span>
            <span className="ds">{t("默认关闭。下面选中的情形出现时放一声短提示，不依赖系统通知，也不受系统通知静音的影响。窗口缩小、在后台时也会响")}</span>
          </span>
          <Switch data-action="notify.sound" on={prefs.sound} label={t("播放提示音")} onClick={() => flip({ sound: !prefs.sound })} />
        </div>
        {/* Drawn as branches of the first rather than as a rule you discover by
            watching three switches grey out together. */}
        <div className="lrow subrow" data-off={active ? undefined : ""}>
          <span className="tx">
            <span className="lb">{t("这一轮结束时")}</span>
            <span className="ds">{t("回答写完、或者这一轮失败了")}</span>
          </span>
          <Switch data-action="notify.turn-done" on={prefs.turnDone} busy={!active} label={t("这一轮结束时")} onClick={() => flip({ turnDone: !prefs.turnDone })} />
        </div>
        <div className="lrow subrow" data-off={active ? undefined : ""}>
          <span className="tx">
            <span className="lb">{t("有操作等待批准时")}</span>
            <span className="ds">{t("模型要动的东西超出了已经给出的授权，正停在那里等你")}</span>
          </span>
          <Switch data-action="notify.approval" on={prefs.approval} busy={!active} label={t("有操作等待批准时")} onClick={() => flip({ approval: !prefs.approval })} />
        </div>
        <div className="lrow subrow" data-off={active ? undefined : ""}>
          <span className="tx">
            <span className="lb">{t("模型提问时")}</span>
            <span className="ds">{t("模型停下来问你一个问题，没有答案它就不会往下走")}</span>
          </span>
          <Switch data-action="notify.ask" on={prefs.ask} busy={!active} label={t("模型提问时")} onClick={() => flip({ ask: !prefs.ask })} />
        </div>
        <div className="lrow subrow" data-off={active ? undefined : ""}>
          <span className="tx">
            <span className="lb">{t("反馈有新回复时")}</span>
            <span className="ds">{t("你提交的反馈收到回复、或需要你补充信息时提醒一次，不带回复内容")}</span>
          </span>
          <Switch data-action="notify.feedback-reply" on={prefs.feedbackReply} busy={!active} label={t("反馈有新回复时")} onClick={() => flip({ feedbackReply: !prefs.feedbackReply })} />
        </div>
        <div className="lrow subrow" data-off={active ? undefined : ""}>
          <span className="tx">
            <span className="lb">{t("磁盘空间不足时")}</span>
            <span className="ds">{t("项目或会话所在的磁盘剩余空间很少时提醒（不足 5 GB 或不足 3%），同一块盘长时间不重复提醒；只提醒，不会清理任何文件")}</span>
          </span>
          <Switch data-action="notify.disk-low" on={prefs.diskLow} busy={!active} label={t("磁盘空间不足时")} onClick={() => flip({ diskLow: !prefs.diskLow })} />
        </div>
      </div>
      <ApplyNote id="notify" />
    </section>
  );
}
