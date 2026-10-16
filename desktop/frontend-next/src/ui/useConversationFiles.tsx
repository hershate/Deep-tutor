import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type KeyboardEvent } from "react";
import type { AgentPort } from "../port/port";
import { host } from "../port/host";
import type { LocalRefs } from "./Markdown";
import { conversationFile } from "./filelinks";
import { WorkbenchFileMenu, absoluteWorkspacePath, type FileMenu } from "./WorkbenchFileMenu";

export function useConversationFiles(port: AgentPort, root: string, remote: boolean, shown: boolean, reveal: () => void, fail: (e: unknown) => void) {
  const [request, setRequest] = useState<{ path: string; port: AgentPort; root: string }>();
  const [menu, setMenu] = useState<(FileMenu & { id: number; port: AgentPort; root: string }) | null>(null);
  const menuID = useRef(0);
  const [platform, setPlatform] = useState("");
  const close = useCallback(() => setMenu(null), []);
  useEffect(close, [port, root, shown, close]);
  useEffect(() => { void host().describe().then((h) => setPlatform(h.platform)).catch(() => {}); }, []);
  const refs = useMemo<LocalRefs>(() => {
    const path = (href: string) => conversationFile(root, href);
    const offer = (e: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>, href: string) => {
      const file = path(href);
      if (!file || document.getSelection()?.isCollapsed === false) return;
      e.preventDefault();
      e.stopPropagation();
      const rect = e.currentTarget.getBoundingClientRect();
      const keyed = !("clientX" in e) || (e.clientX === 0 && e.clientY === 0);
      setMenu({ id: ++menuID.current, port, root, path: file, folder: false, anchor: e.currentTarget,
        x: keyed ? rect.left : e.clientX, y: keyed ? rect.bottom : e.clientY });
    };
    return {
      path,
      title: (href) => { const file = path(href); return file ? absoluteWorkspacePath(root, file) : ""; },
      link: (href) => {
        const file = path(href);
        return file ? () => { setRequest({ path: file, port, root }); reveal(); } : null;
      },
      image: () => null,
      menu: offer,
    };
  }, [port, root, reveal]);
  const menuView = shown && menu?.port === port && menu.root === root && <WorkbenchFileMenu key={menu.id} menu={menu} root={root} platform={platform}
    revealable={!remote && port.revealsFiles()} onClose={close}
    onOpen={!remote && port.opensFiles() ? (path) => { void port.openFile(path).catch(fail); } : undefined}
    onReveal={(path) => { void port.revealInFileManager(path).catch(fail); }} />;
  return { refs, request: request?.port === port && request.root === root ? request : undefined, menuView };
}
