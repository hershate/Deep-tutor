import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorSearchResults } from "../../port/tutor";
import type { TutorView } from "./Tutor";

// Global search over plans, knowledge nodes, notes, and KB documents.
// Grouped results with counts.
interface Props {
  port: AgentPort;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
  initialQ?: string;
}

export function SearchView({ port, onGo, onError, initialQ }: Props) {
  const [q, setQ] = useState(initialQ ?? "");
  const [results, setResults] = useState<TutorSearchResults | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults(null);
      return;
    }
    setBusy(true);
    const timer = setTimeout(() => {
      port.tutorSearch(q.trim(), 10).then(setResults).catch((e) => {
        onError(reason(e));
        setResults(null);
      }).finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, port, onError]);

  const hit = (text: string) => text.toLowerCase().includes(q.trim().toLowerCase());

  return (
    <div className="tut-page">
      <div className="tut-card">
        <input
          className="tut-search" data-action="tutor.search-q" type="search"
          placeholder={t("搜索方案、知识点、笔记…")} value={q}
          onChange={(e) => setQ(e.target.value)} autoFocus
        />
        {busy && <p className="tut-dim">{t("搜索中…")}</p>}
        {results && (
          <>
            <section>
              <h4>{t("方案 ({n})", { n: results.plans.total })}</h4>
              {results.plans.items.map((p) => (
                <div className="tut-row" key={p.id}>
                  <button type="button" className="tut-row-main" data-action="tutor.open-plan" onClick={() => onGo({ k: "plan", pid: p.id })}>
                    {hit(p.topic ?? "") || hit(p.title ?? "") ? <mark>{p.topic || p.title}</mark> : p.topic || p.title}
                  </button>
                </div>
              ))}
            </section>
            <section>
              <h4>{t("知识节点 ({n})", { n: results.nodes.total })}</h4>
              {results.nodes.items.map((n) => (
                <div className="tut-row" key={`${n.plan_id}-${n.id}`}>
                  <button type="button" className="tut-row-main" data-action="tutor.open-plan" onClick={() => onGo({ k: "plan", pid: n.plan_id })}>
                    {hit(n.name) ? <mark>{n.name}</mark> : n.name}
                    <span className="tut-dim"> · {n.plan_topic}</span>
                  </button>
                </div>
              ))}
            </section>
            <section>
              <h4>{t("笔记 ({n})", { n: results.notes.total })}</h4>
              {results.notes.items.map((n) => (
                <div className="tut-row" key={n.id}>
                  <button type="button" className="tut-row-main" data-action="tutor.note-open" onClick={() => onGo({ k: "notes" })}>
                    {hit(n.title) ? <mark>{n.title}</mark> : n.title}
                    {n.snippet && <span className="tut-dim"> — {n.snippet}</span>}
                  </button>
                </div>
              ))}
            </section>
            <section>
              <h4>{t("知识库文档 ({n})", { n: results.kb_docs.total })}</h4>
              {results.kb_docs.items.map((d) => (
                <div className="tut-row" key={d.id}>
                  <button type="button" className="tut-row-main" data-action="tutor.kb-open" onClick={() => onGo({ k: "kb", doc: d.id })}>
                    {hit(d.title) ? <mark>{d.title}</mark> : d.title}
                    {d.snippet && <span className="tut-dim"> — {d.snippet}</span>}
                  </button>
                </div>
              ))}
            </section>
          </>
        )}
        {results && results.plans.total + results.nodes.total + results.notes.total + results.kb_docs.total === 0 && (
          <p className="tut-dim">{t("没有匹配的结果。")}</p>
        )}
      </div>
    </div>
  );
}
