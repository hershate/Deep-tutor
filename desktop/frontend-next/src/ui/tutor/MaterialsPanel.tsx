import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import { LazyMarkdown } from "../LazyMarkdown";
import type { AgentPort } from "../../port/port";
import type { TutorMaterialsSnapshot } from "../../port/tutor";
import * as engine from "./engine";
import type { MaterialsProgress } from "./engine";

// The self-study guide tab: the persisted snapshot renders without a model;
// the generate button streams unit_start/unit_done and refreshes the
// snapshot at the end. Budget stoppage keeps the units that landed.
interface Props {
  port: AgentPort;
  pid: string;
  onError: (message: string) => void;
}

export function MaterialsPanel({ port, pid, onError }: Props) {
  const [snap, setSnap] = useState<TutorMaterialsSnapshot | null>(null);
  const [progress, setProgress] = useState<MaterialsProgress | null>(null);

  const reload = () => {
    port.tutorStudyMaterials(pid, {}).then(setSnap).catch((e) => onError(reason(e)));
  };
  useEffect(reload, [port, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = async (req: { refresh?: boolean; unit_ids?: string[] }) => {
    if (progress && !progress.finished) return;
    setProgress({ total: 0, doneCount: 0, degradedCount: 0, finished: false });
    await engine.runMaterials(port, pid, req, setProgress);
    reload();
  };

  const view = progress && !progress.finished ? progress : null;

  const downloadUnitSlides = async (uid: string) => {
    try {
      const text = await port.tutorUnitSlides(pid, uid);
      const blob = new Blob([text], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `unit-${uid}.slides.html`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  return (
    <div className="tut-card">
      <header className="tut-plan-hd">
        <h3>{t("自学讲义")}</h3>
        <div>
          {snap && snap.unit_count > 0 && (
            <button type="button" data-action="tutor.mat-refresh" onClick={() => void generate({ refresh: true })}>
              {t("全部重写")}
            </button>
          )}
          <button
            type="button" className="tut-primary" data-action="tutor.mat-gen"
            disabled={!!view}
            onClick={() => void generate({})}
          >
            {view ? t("生成中…") : snap && snap.unit_count > 0 ? t("补齐缺失单元") : t("生成讲义")}
          </button>
        </div>
      </header>

      {view && (
        <div className="tut-mat-progress" aria-live="polite">
          {view.current
            ? t("正在写第 {order} 单元：{title}", { order: view.current.order, title: view.current.title })
            : t("已完成 {done}/{total} 单元", { done: view.doneCount, total: view.total || "…" })}
          {view.doneCount > 0 && <span className="tut-dim"> · {t("降级 {n}", { n: view.degradedCount })}</span>}
        </div>
      )}
      {progress?.finished && progress.errorCode && (
        <p className="tut-warn">{progress.errorMessage ?? t("生成中断，已生成的单元已保留。")}</p>
      )}

      {snap === null && <p className="tut-dim">{t("读取中…")}</p>}
      {snap && snap.unit_count === 0 && !view && (
        <p className="tut-dim">{t("还没有讲义。讲义按 55 分钟一个单元切分知识点，逐单元撰写并附已验证的补充资源。")}</p>
      )}
      {snap && snap.unit_count > 0 && (
        <>
          {snap.units && snap.units.some((u) => u.degraded) && (
            <p className="tut-warn">{t("有单元生成失败，已降级为占位大纲；可单独重写。")}</p>
          )}
          {snap.units?.map((u) => (
            <details key={u.id} className="tut-stage" open={u.order === 1}>
              <summary>
                <strong>{t("单元 {n}", { n: u.order })} · {u.title}</strong>
                {u.degraded && <span className="tut-warn"> · {t("降级")}</span>}
                {u.estimated_time && <span className="tut-dim"> · {u.estimated_time}</span>}
              </summary>
              <LazyMarkdown text={u.content_md} />
              <button
                type="button" data-action="tutor.mat-unit-slides"
                onClick={() => void downloadUnitSlides(u.id)}
              >
                {t("导出幻灯片")}
              </button>
              {u.degraded && (
                <button
                  type="button" data-action="tutor.mat-unit-redo"
                  onClick={() => void generate({ unit_ids: [u.id] })}
                >
                  {t("重写这个单元")}
                </button>
              )}
            </details>
          ))}
        </>
      )}
    </div>
  );
}
