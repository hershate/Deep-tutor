import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AgentPort, WorkspaceFile } from "../port/port";
import { reason } from "../i18n/kernel";

type Entry = {
  file: WorkspaceFile | null; draft: string; diff: string; error: string;
  pending: "" | "read" | "save"; issued: number;
};
const blank = (): Entry => ({ file: null, draft: "", diff: "", error: "", pending: "", issued: 0 });
const dirty = (entry: Entry) => !!entry.file && entry.draft !== entry.file.content;

export function useWorkbenchFiles({ port, workspaceRoot, path, openPaths, shown, refresh, poll }: {
  port: AgentPort; workspaceRoot: string; path: string; openPaths: string[]; shown: boolean; refresh: string; poll: boolean;
}) {
  const entries = useMemo(() => new Map<string, Entry>(), [port, workspaceRoot]);
  const current = useRef<Map<string, Entry> | null>(entries);
  current.current = entries;
  const [version, render] = useState(0);
  const notify = useCallback(() => { if (current.current === entries) render((n) => n + 1); }, [entries]);
  const entry = path ? entries.get(path) ?? blank() : blank();
  if (path && !entries.has(path)) entries.set(path, entry);

  useEffect(() => {
    current.current = entries;
    return () => { current.current = null; };
  }, [entries]);
  useEffect(() => {
    for (const [path, entry] of entries) {
      if (!openPaths.includes(path) && !dirty(entry) && entry.pending !== "save") entries.delete(path);
    }
  }, [entries, openPaths, version]);

  const read = useCallback((quiet = false) => {
    if (!path) return;
    const entry = entries.get(path)!;
    let live = true;
    if (entry.pending === "save" || dirty(entry) || (entry.file && !shown) || (quiet && entry.pending === "read")) return;
    const ticket = ++entry.issued;
    let changed = !quiet || !entry.file;
    void (async () => {
      const valid = () => live && current.current === entries && ticket === entry.issued;
      entry.pending = "read";
      if (!entry.file) entry.error = "";
      if (changed) notify();
      try {
        const [file, patch] = await Promise.all([
          port.workspaceFile(path),
          quiet ? Promise.resolve(null) : port.changeDiff(path).catch(() => null),
        ]);
        if (!valid() || dirty(entry)) return;
        changed ||= entry.file?.content !== file.content || entry.file?.revision !== file.revision || !!entry.error;
        entry.file = file;
        entry.draft = file.content;
        entry.error = "";
        if (!quiet) entry.diff = patch?.diff ?? "";
      } catch (e) {
        if (valid() && !quiet) entry.error = reason(e);
      } finally {
        if (valid()) { entry.pending = ""; if (changed) notify(); }
      }
    })();
    return () => {
      live = false;
      if (ticket === entry.issued) entry.pending = "";
    };
  }, [port, entries, path, shown, notify]);
  useEffect(() => read(), [read, refresh]);
  useEffect(() => {
    if (!poll || !shown) return;
    let cancel: (() => void) | undefined;
    const timer = window.setInterval(() => { cancel = read(true) ?? cancel; }, 3000);
    return () => { cancel?.(); window.clearInterval(timer); };
  }, [read, poll, shown]);

  const setDraft = (draft: string) => {
    if (current.current !== entries || !entry.file) return;
    entry.draft = draft;
    notify();
  };
  const save = async () => {
    if (current.current !== entries || !entry.file || !dirty(entry) || entry.pending === "save") return;
    const sent = { ...entry.file, content: entry.draft };
    const ticket = ++entry.issued;
    entry.pending = "save";
    entry.error = "";
    notify();
    try {
      const file = await port.saveWorkspaceFile(sent);
      if (current.current !== entries || ticket !== entry.issued) return;
      entry.file = file;
      if (entry.draft === sent.content) entry.draft = file.content;
    } catch (e) {
      if (current.current === entries && ticket === entry.issued) entry.error = reason(e);
    } finally {
      if (current.current === entries && ticket === entry.issued) { entry.pending = ""; notify(); }
    }
  };
  return { ...entry, busy: entry.pending === "save" || (!entry.file && entry.pending === "read"), setDraft, save };
}
