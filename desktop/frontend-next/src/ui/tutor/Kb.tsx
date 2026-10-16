import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import { LazyMarkdown } from "../LazyMarkdown";
import type { AgentPort } from "../../port/port";
import type { TutorKbChatRecord, TutorKbChatResult, TutorKbDoc, TutorKbParsedText } from "../../port/tutor";
import * as engine from "./engine";
import { useConfirm } from "./Confirm";
import type { TutorView } from "./Tutor";

// The knowledge base: a document library independent of plan sessions plus
// the chat that answers strictly from it. Documents flow
// upload→confirm→parse (text and URL land parsed); the chat carries
// citations back to the documents it used.

const KIND_LABEL: Record<string, string> = {
  file: "文件", text: "文本", url: "网页", image: "图片", audio: "音频", video: "视频",
};
const STATUS_LABEL: Record<string, string> = {
  pending: "暂存待确认", confirmed: "已添加", parsed: "已解析",
};

interface Props {
  port: AgentPort;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
  openDoc?: string;
}

export function Kb({ port, onGo, onError, openDoc }: Props) {
  const [tab, setTab] = useState<"docs" | "chat">("docs");
  const [docs, setDocs] = useState<TutorKbDoc[] | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<TutorKbDoc | null>(null);
  const [parsed, setParsed] = useState<TutorKbParsedText | null>(null);
  const [rename, setRename] = useState("");
  const [showText, setShowText] = useState(false);
  const [textTitle, setTextTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [showURL, setShowURL] = useState(false);
  const [urlField, setUrlField] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { confirm, overlay } = useConfirm();

  useEffect(() => {
    setBusy(true);
    const timer = setTimeout(() => {
      port.tutorKbDocs(q.trim() || undefined, kind || undefined).then(setDocs).catch((e) => {
        onError(reason(e));
        setDocs([]);
      }).finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, kind, port, onError]);

  // A search hit can name the document to open.
  useEffect(() => {
    if (!openDoc || docs === null) return;
    const doc = docs.find((d) => d.id === openDoc);
    if (doc) openDetail(doc);
  }, [openDoc, docs]); // eslint-disable-line react-hooks/exhaustive-deps

  const reload = () => {
    port.tutorKbDocs(q.trim() || undefined, kind || undefined)
      .then(setDocs)
      .catch((e) => onError(reason(e)));
  };

  const openDetail = (doc: TutorKbDoc) => {
    setDetail(doc);
    setRename(doc.title);
    setParsed(null);
    if (doc.status === "parsed") {
      port.tutorKbParsed(doc.id).then(setParsed).catch((e) => onError(reason(e)));
    }
  };

  const afterWrite = (doc: TutorKbDoc) => {
    if (doc.meta.deduped) onError(t("已存在相同内容的文档，直接复用。"));
    if (detail && doc.id === detail.id) openDetail(doc);
    reload();
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const buf = await f.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let binary = "";
        for (let i = 0; i < bytes.length; i += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        }
        const doc = await port.tutorKbUpload(f.name, btoa(binary));
        afterWrite(doc);
      }
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const addText = async () => {
    if (textBody.trim() === "") return;
    setBusy(true);
    try {
      const doc = await port.tutorKbAddText(textTitle.trim(), textBody);
      setTextTitle("");
      setTextBody("");
      setShowText(false);
      afterWrite(doc);
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  const addURL = async () => {
    if (urlField.trim() === "") return;
    setBusy(true);
    try {
      const doc = await port.tutorKbAddURL(urlField.trim());
      setUrlField("");
      setShowURL(false);
      afterWrite(doc);
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<TutorKbDoc>) => {
    try {
      afterWrite(await fn());
    } catch (e) {
      onError(reason(e));
    }
  };

  const remove = async (doc: TutorKbDoc) => {
    if (!(await confirm(t("删除文档「{title}」？", { title: doc.title })))) return;
    try {
      await port.tutorKbDelete(doc.id);
      setSelected((ids) => ids.filter((id) => id !== doc.id));
      if (detail?.id === doc.id) setDetail(null);
      reload();
    } catch (e) {
      onError(reason(e));
    }
  };

  const saveRename = async () => {
    if (!detail || rename.trim() === "") return;
    try {
      const doc = await port.tutorKbRename(detail.id, rename.trim());
      setDetail(doc);
      reload();
    } catch (e) {
      onError(reason(e));
    }
  };

  const exportZip = async () => {
    try {
      const blob = await port.tutorKbExportZip(selected.length ? selected : undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `kb-export-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  const generate = async () => {
    if (selected.length === 0) return;
    setBusy(true);
    try {
      const result = await port.tutorKbGenerate(selected);
      setSelected([]);
      // The endpoint only seeds the session with the documents' parsed text;
      // the run itself starts here, the ordinary generate flow, or the page
      // would land on a session with no ledger entry and nothing running.
      const titles = selected
        .map((id) => (docs ?? []).find((d) => d.id === id)?.title)
        .filter((title): title is string => !!title)
        .slice(0, 5)
        .map((title) => `《${title}》`)
        .join("");
      const goal = titles !== "" ? `基于所选知识库资料制定学习方案：${titles}` : "基于知识库资料制定学习方案";
      onGo({ k: "generate", sid: result.session_id });
      void engine.startGenerate(port, result.session_id, goal);
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleSelect = (id: string) => {
    setSelected((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : ids.length >= 20 ? ids : [...ids, id]);
  };

  return (
    <div className="tut-page tut-kb">
      <div className={`tut-kb-list${detail ? " tut-kb-narrow" : ""}`}>
        <div className="tut-card">
          <header className="tut-plan-hd">
            <h3>{t("知识库")}</h3>
            <div className="tut-kb-tabs" role="tablist">
              <button type="button" role="tab" data-action="tutor.kb-tab" aria-selected={tab === "docs"} onClick={() => setTab("docs")}>{t("文档")}</button>
              <button type="button" role="tab" data-action="tutor.kb-tab" aria-selected={tab === "chat"} onClick={() => setTab("chat")}>{t("问答")}</button>
            </div>
          </header>

          {tab === "docs" && (
            <>
              <div className="tut-kb-toolbar">
                <input
                  className="tut-search" data-action="tutor.kb-search" type="search"
                  placeholder={t("搜索标题或解析内容…")} value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
                <select data-action="tutor.kb-kind" value={kind} onChange={(e) => setKind(e.target.value)} aria-label={t("类型")}>
                  <option value="">{t("全部类型")}</option>
                  {Object.entries(KIND_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>{t(label)}</option>
                  ))}
                </select>
              </div>
              <div className="tut-acts">
                <button type="button" data-action="tutor.kb-upload" disabled={busy} onClick={() => fileInput.current?.click()}>{t("上传文件")}</button>
                <input
                  ref={fileInput} type="file" hidden multiple data-action="tutor.kb-file"
                  onChange={(e) => void uploadFiles(e.target.files)}
                />
                <button type="button" data-action="tutor.kb-text-toggle" onClick={() => { setShowText(!showText); setShowURL(false); }}>{t("添加文本")}</button>
                <button type="button" data-action="tutor.kb-url-toggle" onClick={() => { setShowURL(!showURL); setShowText(false); }}>{t("添加网页")}</button>
                {(docs?.length ?? 0) > 0 && <button type="button" data-action="tutor.kb-export" onClick={() => void exportZip()}>{t("导出 ZIP")}</button>}
                {selected.length > 0 && (
                  <button type="button" className="tut-primary" data-action="tutor.kb-generate" disabled={busy} onClick={() => void generate()}>
                    {t("用所选生成方案（{n}）", { n: selected.length })}
                  </button>
                )}
              </div>
              {showText && (
                <div className="tut-kb-add">
                  <input data-action="tutor.kb-text-title" placeholder={t("标题（可空，取正文首行）")} value={textTitle} maxLength={256} onChange={(e) => setTextTitle(e.target.value)} />
                  <textarea data-action="tutor.kb-text-body" rows={4} placeholder={t("粘贴文本内容")} value={textBody} onChange={(e) => setTextBody(e.target.value)} />
                  <div className="tut-acts">
                    <button type="button" className="tut-primary" data-action="tutor.kb-text-save" disabled={busy || textBody.trim() === ""} onClick={() => void addText()}>{t("入库")}</button>
                  </div>
                </div>
              )}
              {showURL && (
                <div className="tut-kb-add">
                  <input data-action="tutor.kb-url-field" placeholder="https://…" value={urlField} onChange={(e) => setUrlField(e.target.value)} />
                  <div className="tut-acts">
                    <button type="button" className="tut-primary" data-action="tutor.kb-url-save" disabled={busy || urlField.trim() === ""} onClick={() => void addURL()}>{t("抓取入库")}</button>
                  </div>
                </div>
              )}

              {docs === null && <p className="tut-dim">{t("读取中…")}</p>}
              {docs?.length === 0 && <p className="tut-dim">{t("知识库还是空的：上传文件、粘贴文本或收藏网页。")}</p>}
              {docs?.map((d) => (
                <div className="tut-row" key={d.id}>
                  <input
                    type="checkbox" data-action="tutor.kb-select" checked={selected.includes(d.id)}
                    aria-label={t("选择 {title}", { title: d.title })} onChange={() => toggleSelect(d.id)}
                  />
                  <button type="button" className="tut-row-main" data-action="tutor.kb-open" onClick={() => openDetail(d)}>
                    {d.title}
                    <span className="tut-dim">
                      {" · "}{t(KIND_LABEL[d.kind] ?? d.kind)} · {t(STATUS_LABEL[d.status] ?? d.status)}
                      {d.parsed_chars ? ` · ${d.parsed_chars}${t("字")}` : ""}
                    </span>
                  </button>
                  {d.status === "pending" && <button type="button" className="tut-row-act" data-action="tutor.kb-confirm" onClick={() => void act(() => port.tutorKbConfirm(d.id))}>{t("确认")}</button>}
                  {d.status !== "parsed" && d.has_original && <button type="button" className="tut-row-act" data-action="tutor.kb-parse" onClick={() => void act(() => port.tutorKbParse(d.id, "full"))}>{t("解析")}</button>}
                  {d.status === "parsed" && d.has_original && <button type="button" className="tut-row-act" data-action="tutor.kb-parse" onClick={() => void act(() => port.tutorKbParse(d.id, "fast"))}>{t("快速重解析")}</button>}
                  <button type="button" className="tut-row-act" data-action="tutor.kb-del" onClick={() => void remove(d)}>{t("删除")}</button>
                </div>
              ))}
            </>
          )}

          {tab === "chat" && <KbChat port={port} docs={docs ?? []} selected={selected} onToggle={toggleSelect} onError={onError} askDelete={confirm} />}
        </div>
      </div>

      {detail && (
        <div className="tut-card tut-kb-detail">
          <header className="tut-plan-hd">
            <h3>{detail.title}</h3>
            <button type="button" data-action="tutor.kb-close" onClick={() => setDetail(null)}>{t("关闭")}</button>
          </header>
          <p className="tut-dim">
            {t(KIND_LABEL[detail.kind] ?? detail.kind)}
            {" · "}{t(STATUS_LABEL[detail.status] ?? detail.status)}
            {detail.ext ? ` · .${detail.ext}` : ""}
            {detail.parsed_chars ? ` · ${detail.parsed_chars}${t("字")}` : ""}
            {detail.meta.truncated ? ` · ${t("超出上限已截断")}` : ""}
            {detail.meta.deduped ? ` · ${t("复用已有文档")}` : ""}
          </p>
          {detail.error && <p className="tut-warn">{t("上次解析失败：{err}", { err: detail.error })}</p>}
          <div className="tut-kb-rename">
            <input data-action="tutor.kb-rename" value={rename} maxLength={256} onChange={(e) => setRename(e.target.value)} aria-label={t("标题")} />
            <button type="button" data-action="tutor.kb-rename-save" disabled={rename.trim() === "" || rename.trim() === detail.title} onClick={() => void saveRename()}>{t("改名")}</button>
          </div>
          <div className="tut-acts">
            {detail.status === "pending" && <button type="button" data-action="tutor.kb-confirm" onClick={() => void act(() => port.tutorKbConfirm(detail.id))}>{t("确认添加")}</button>}
            {detail.has_original && detail.status !== "parsed" && <button type="button" data-action="tutor.kb-parse" onClick={() => void act(() => port.tutorKbParse(detail.id, "full"))}>{t("解析（完整）")}</button>}
            {detail.has_original && detail.status === "parsed" && (
              <>
                <button type="button" data-action="tutor.kb-parse" onClick={() => void act(() => port.tutorKbParse(detail.id, "full"))}>{t("重新解析（完整）")}</button>
                <button type="button" data-action="tutor.kb-parse" onClick={() => void act(() => port.tutorKbParse(detail.id, "fast"))}>{t("重新解析（快速）")}</button>
              </>
            )}
            {detail.has_original && <a href={port.tutorKbRawHref(detail.id)} target="_blank" rel="noreferrer">{t("查看原件")}</a>}
            {detail.has_original && <a href={port.tutorKbRawHref(detail.id, true)}>{t("下载原件")}</a>}
          </div>
          {parsed && (
            <div className="tut-kb-parsed">
              <h4>{t("解析文本")}{parsed.truncated ? t("（已截断）") : ""}</h4>
              <LazyMarkdown text={parsed.text} />
            </div>
          )}
          {detail.status === "parsed" && !parsed && <p className="tut-dim">{t("读取中…")}</p>}
          {detail.status !== "parsed" && <p className="tut-dim">{t("确认并解析后，这里会展示提取的文本。")}</p>}
        </div>
      )}

      {overlay}
    </div>
  );
}

// KbChat: the question box over the selected documents (empty = the newest
// 20 parsed), the answer with its citations, and the persisted history.
function KbChat({ port, docs, selected, onToggle, onError, askDelete }: {
  port: AgentPort;
  docs: TutorKbDoc[];
  selected: string[];
  onToggle: (id: string) => void;
  onError: (message: string) => void;
  askDelete: (message: string) => Promise<boolean>;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<TutorKbChatResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [history, setHistory] = useState<TutorKbChatRecord[] | null>(null);
  const [openRecord, setOpenRecord] = useState<TutorKbChatRecord | null>(null);

  const reloadHistory = () => {
    port.tutorKbChats().then((r) => setHistory(r.items)).catch((e) => {
      onError(reason(e));
      setHistory([]);
    });
  };
  useEffect(reloadHistory, [port]); // eslint-disable-line react-hooks/exhaustive-deps

  const parsedDocs = docs.filter((d) => d.status === "parsed");

  const ask = async () => {
    if (question.trim() === "" || asking) return;
    setAsking(true);
    setAnswer(null);
    setOpenRecord(null);
    try {
      const result = await port.tutorKbChat(question.trim(), selected.length ? selected : undefined);
      setAnswer(result);
      setQuestion("");
      reloadHistory();
    } catch (e) {
      onError(reason(e));
    } finally {
      setAsking(false);
    }
  };

  const removeRecord = async (r: TutorKbChatRecord) => {
    if (!(await askDelete(t("删除这条问答记录？")))) return;
    try {
      await port.tutorKbChatDelete(r.id);
      if (openRecord?.id === r.id) setOpenRecord(null);
      reloadHistory();
    } catch (e) {
      onError(reason(e));
    }
  };

  return (
    <>
      {parsedDocs.length === 0 ? (
        <p className="tut-dim">{t("先在「文档」里上传并解析一些资料，再来提问。")}</p>
      ) : (
        <div className="tut-kb-chips">
          {parsedDocs.map((d) => (
            <button
              type="button" key={d.id} data-action="tutor.kb-chat-doc"
              className={selected.includes(d.id) ? "tut-chip tut-chip-on" : "tut-chip"}
              onClick={() => onToggle(d.id)}
            >
              {d.title}
            </button>
          ))}
        </div>
      )}
      <p className="tut-dim">
        {selected.length === 0
          ? t("未选择文档时，默认使用最近 20 篇已解析文档。")
          : t("已选 {n} 篇（最多 20 篇）", { n: selected.length })}
      </p>
      <textarea
        data-action="tutor.kb-chat-q" rows={3} maxLength={4000}
        placeholder={t("就知识库内容提问…")} value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />
      <div className="tut-acts">
        <button type="button" className="tut-primary" data-action="tutor.kb-chat-ask" disabled={asking || question.trim() === ""} onClick={() => void ask()}>
          {asking ? t("思考中…") : t("提问")}
        </button>
      </div>

      {answer && <KbAnswerCard result={answer} />}

      {history !== null && history.length > 0 && (
        <section className="tut-kb-history">
          <h4>{t("问答记录")}</h4>
          {history.map((r) => (
            <div className="tut-row" key={r.id}>
              <button type="button" className="tut-row-main" data-action="tutor.kb-chat-open" onClick={() => { setOpenRecord(r); setAnswer(null); }}>
                {r.question}
                <span className="tut-dim"> · {new Date(r.created_at).toLocaleString()}</span>
              </button>
              <button type="button" className="tut-row-act" data-action="tutor.kb-chat-del" onClick={() => void removeRecord(r)}>{t("删除")}</button>
            </div>
          ))}
        </section>
      )}
      {openRecord && (
        <div className="tut-kb-answer">
          <h4>{openRecord.question}</h4>
          <LazyMarkdown text={openRecord.answer} />
          {openRecord.citations.length > 0 && (
            <p className="tut-dim">
              {t("引用：")}
              {openRecord.citations.map((c) => `《${c.title}》`).join(" ")}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function KbAnswerCard({ result }: { result: TutorKbChatResult }) {
  return (
    <div className="tut-kb-answer">
      <LazyMarkdown text={result.answer} />
      {result.citations.length > 0 && (
        <div className="tut-kb-chips">
          {result.citations.map((c) => (
            <span className="tut-chip" key={c.doc_id}>{"《" + c.title + "》"}</span>
          ))}
        </div>
      )}
      {result.omitted_titles.length > 0 && (
        <p className="tut-dim">{t("因篇幅上限未注入：{titles}", { titles: result.omitted_titles.map((s) => `《${s}》`).join(" ") })}</p>
      )}
      <p className="tut-dim">{t("参考文档 {n} 篇", { n: result.context_docs })}</p>
    </div>
  );
}
