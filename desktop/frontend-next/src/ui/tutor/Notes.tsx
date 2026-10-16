import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import { LazyMarkdown } from "../LazyMarkdown";
import type { AgentPort } from "../../port/port";
import type { TutorNote } from "../../port/tutor";
import { useConfirm } from "./Confirm";

// Free notes: list + editor with live preview, plus the portable ZIP
// export/import that round-trips the note .md format.
interface Props {
  port: AgentPort;
  onError: (message: string) => void;
}

export function Notes({ port, onError }: Props) {
  const [notes, setNotes] = useState<TutorNote[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { confirm, overlay } = useConfirm();

  const reload = () => {
    port.tutorNotes().then(setNotes).catch((e) => {
      onError(reason(e));
      setNotes([]);
    });
  };
  useEffect(reload, [port]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (n: TutorNote | null) => {
    setOpenId(n?.id ?? "new");
    setTitle(n?.title ?? "");
    setBody(n?.body ?? "");
    setDirty(false);
  };

  // open(null) is the new-note editor, not a closed one, so closing is its own
  // reset: the editor leaves, and it takes its fields with it.
  const close = () => {
    setOpenId(null);
    setTitle("");
    setBody("");
    setDirty(false);
  };

  const save = async () => {
    if (busy) return;
    if (title.trim() === "" && body.trim() === "") {
      onError(t("标题和正文至少要有一个"));
      return;
    }
    setBusy(true);
    try {
      const saved = openId && openId !== "new"
        ? await port.tutorNoteSave(openId, title, body)
        : await port.tutorNoteCreate(title, body);
      setOpenId(saved.id);
      setDirty(false);
      reload();
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (n: TutorNote) => {
    if (!(await confirm(t("删除这条笔记？")))) return;
    try {
      await port.tutorNoteDelete(n.id);
      if (openId === n.id) close();
      reload();
    } catch (e) {
      onError(reason(e));
    }
  };

  const exportZip = async () => {
    try {
      const blob = await port.tutorNotesZip();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `notes-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  const importFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    try {
      const payload: { name: string; contentBase64: string }[] = [];
      for (const f of Array.from(files)) {
        const buf = await f.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let binary = "";
        for (let i = 0; i < bytes.length; i += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        }
        payload.push({ name: f.name, contentBase64: btoa(binary) });
      }
      const result = await port.tutorNotesImport(payload);
      onError(t("导入 {n} 条，跳过 {s} 条{quota}", {
        n: result.imported, s: result.skipped,
        quota: result.quota_full ? t("（笔记数已满）") : "",
      }));
      reload();
    } catch (e) {
      onError(reason(e));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const editing = openId !== null;

  return (
    <div className="tut-page tut-notes">
      <div className={`tut-notes-list${editing ? " tut-notes-narrow" : ""}`}>
        <div className="tut-card">
          <header className="tut-plan-hd">
            <h3>{t("笔记")}</h3>
            <div>
              <button type="button" data-action="tutor.note-new" onClick={() => open(null)}>{t("新建")}</button>
              {(notes?.length ?? 0) > 0 && <button type="button" data-action="tutor.note-export" onClick={() => void exportZip()}>{t("导出 ZIP")}</button>}
              <button type="button" data-action="tutor.note-import" disabled={busy} onClick={() => fileInput.current?.click()}>{t("导入")}</button>
              <input
                ref={fileInput} type="file" hidden multiple accept=".md,.markdown,.zip"
                data-action="tutor.note-file" onChange={(e) => void importFiles(e.target.files)}
              />
            </div>
          </header>
          {notes === null && <p className="tut-dim">{t("读取中…")}</p>}
          {notes?.length === 0 && <p className="tut-dim">{t("还没有笔记；支持导入 .md 文件或包含 .md 的 zip。")}</p>}
          {notes?.map((n) => (
            <div className="tut-row" key={n.id}>
              <button type="button" className="tut-row-main" data-action="tutor.note-open" onClick={() => open(n)}>
                {n.title || t("无题")}
                <span className="tut-dim"> · {new Date(n.updated_at).toLocaleDateString()}</span>
              </button>
            <button type="button" className="tut-row-act" data-action="tutor.note-del" onClick={() => void remove(n)}>{t("删除")}</button>
          </div>
          ))}
        </div>
      </div>

      {overlay}

      {editing && (
        <div className="tut-card tut-notes-editor">
          <input
            className="tut-note-title" data-action="tutor.note-title" maxLength={256}
            placeholder={t("标题")} value={title}
            onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
          />
          <textarea
            className="tut-note-body" data-action="tutor.note-body" rows={14}
            placeholder={t("正文（Markdown）")} value={body}
            onChange={(e) => { setBody(e.target.value); setDirty(true); }}
          />
          {body.trim() !== "" && <LazyMarkdown text={body} />}
          <div className="tut-acts">
            <button type="button" className="tut-primary" data-action="tutor.note-save" disabled={busy} onClick={() => void save()}>
              {openId === "new" ? t("保存") : t("更新")}
            </button>
            <button type="button" data-action="tutor.note-close" onClick={close}>{t("关闭")}</button>
            {dirty && <span className="tut-dim">{t("有未保存的修改")}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
