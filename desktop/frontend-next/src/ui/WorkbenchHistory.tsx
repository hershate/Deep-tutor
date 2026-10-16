import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "../i18n";
import { reason } from "../i18n/kernel";
import type { AgentPort } from "../port/port";
import type { GitHistory } from "../port/workspace";
import { layoutGraph, type GraphRow } from "../state/gitgraph";
import { since } from "./wallet";

const ROW = 28;
const COL = 12;
const PAD = 8;
const x = (lane: number) => PAD + lane * COL;

type Read = { k: "idle" } | { k: "loading" } | { k: "ready"; history: GitHistory } | { k: "failed"; why: string };

// The commit graph beside the changes: what the project's history looks like,
// read from git on request and never changed from here. It reads when opened and
// again on demand, so a closed section costs nothing.
export function WorkbenchHistory({ port }: { port: AgentPort }) {
  const [shown, setShown] = useState(false);
  const [read, setRead] = useState<Read>({ k: "idle" });
  const latest = useRef(0);
  const load = useCallback(async () => {
    const id = ++latest.current;
    setRead({ k: "loading" });
    try {
      const history = await port.workspaceGitLog(100);
      if (id === latest.current) setRead({ k: "ready", history });
    } catch (e) {
      if (id === latest.current) setRead({ k: "failed", why: reason(e) });
    }
  }, [port]);
  useEffect(() => {
    if (shown && read.k === "idle") void load();
  }, [shown, read.k, load]);
  useEffect(() => () => { latest.current++; }, []);

  const history = read.k === "ready" ? read.history : null;
  const graph = history?.repo ? layoutGraph(history.commits) : null;
  const now = Date.now();
  return (
    <details className="workbench-history" open={shown} onToggle={(e) => setShown(e.currentTarget.open)}>
      <summary>
        <span>{t("提交历史")}</span>
        {graph && <small>{graph.rows.length}</small>}
      </summary>
      {read.k === "loading" && <p className="gh-note" role="status">{t("正在读取…")}</p>}
      {read.k === "failed" && (
        <p className="gh-note refine-error" role="alert">
          {read.why}
          <button type="button" className="btn sm" data-action="workbench.history-reload" onClick={() => void load()}>{t("重新读取")}</button>
        </p>
      )}
      {history && !history.repo && <p className="gh-note">{t("这个文件夹不是 git 仓库")}</p>}
      {graph && graph.rows.length === 0 && <p className="gh-note">{t("还没有提交")}</p>}
      {graph && graph.rows.length > 0 && (
        <ol className="gh-list">
          {graph.rows.map((row) => (
            <li className="gh-row" key={row.commit.id} title={`${row.commit.id.slice(0, 7)}  ${row.commit.subject}`}>
              <Lanes row={row} width={PAD * 2 + Math.max(graph.lanes - 1, 0) * COL} />
              <span className="gh-subject">{row.commit.subject}</span>
              {row.commit.refs.map((ref) => (
                <em className="gh-ref" key={ref} data-kind={ref.startsWith("tag: ") ? "tag" : "branch"}>{ref}</em>
              ))}
              <small className="gh-meta">{row.commit.author} · {since(new Date(row.commit.at * 1000).toISOString(), now)}</small>
            </li>
          ))}
          {history?.truncated && <li className="gh-more">{t("只显示最近的提交")}</li>}
        </ol>
      )}
    </details>
  );
}

const color = (lane: number) => `var(--cat-${(lane % 5) + 1})`;
// A line from (x1, y1) to (x2, y2): straight down a lane, a gentle S across lanes.
const line = (x1: number, y1: number, x2: number, y2: number) =>
  x1 === x2 ? `M${x1} ${y1}V${y2}` : `M${x1} ${y1}C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`;

function Lanes({ row, width }: { row: GraphRow; width: number }) {
  const mid = ROW / 2;
  return (
    <svg className="gh-lanes" width={width} height={ROW} viewBox={`0 0 ${width} ${ROW}`} aria-hidden="true">
      {row.through.map((lane) => <path key={`t${lane}`} d={line(x(lane), 0, x(lane), ROW)} stroke={color(lane)} />)}
      {row.from.map((lane) => <path key={`f${lane}`} d={line(x(lane), 0, x(row.lane), mid)} stroke={color(lane)} />)}
      {row.to.map((lane, n) => <path key={`o${n}`} d={line(x(row.lane), mid, x(lane), ROW)} stroke={color(lane)} />)}
      <circle cx={x(row.lane)} cy={mid} r={4} fill={color(row.lane)} data-merge={row.commit.parents.length > 1 ? "" : undefined} />
    </svg>
  );
}
