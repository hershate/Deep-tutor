import { docRef } from "./docrefs";

export function conversationFile(root: string, href: string): string | null {
  let raw = href.trim();
  if (/^file:/i.test(raw)) {
    try {
      const url = new URL(raw);
      if (url.hostname && url.hostname !== "localhost") return null;
      raw = url.pathname;
      if (/^\/[a-z]:\//i.test(raw)) raw = raw.slice(1);
    } catch { return null; }
  } else if (/^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^[a-z]:[\\/]/i.test(raw)) return null;
  const windows = /^[a-z]:[\\/]/i.test(root);
  const base = (windows ? root.replaceAll("\\", "/") : root).replace(/\/+$/, "");
  if (windows) raw = raw.replaceAll("\\", "/");
  raw = raw.replace(/[?#].*$/, "");
  try { raw = decodeURIComponent(raw); } catch { return null; }
  if (raw.startsWith("//")) return null;
  if (raw.startsWith("/") || /^[a-z]:[\\/]/i.test(raw)) {
    const prefix = base + "/";
    if (!(windows ? raw.toLowerCase().startsWith(prefix.toLowerCase()) : raw.startsWith(prefix))) return null;
    raw = raw.slice(prefix.length);
  }
  // docRef decodes URLs; encode literal filename characters before resolving.
  return docRef("", raw.split("/").map(encodeURIComponent).join("/"));
}

interface Node {
  type: string;
  url?: string;
  value?: string;
  children?: Node[];
}

const TOKEN = /[^\s<>"'`()\[\]{}，。；！？：]+/g;
const FILE = /(?:^|\/)[^/]+\.[a-z0-9]{1,16}$/i;

export function remarkFileLinks(resolve?: (href: string) => string | null) {
  return (tree: Node) => {
    if (!resolve) return;
    const walk = (node: Node) => {
      if (node.type === "link") {
        const path = node.url && resolve(node.url);
        if (path) node.url = path.split("/").map(encodeURIComponent).join("/");
        return;
      }
      if (!node.children) return;
      node.children = node.children.flatMap((child) => {
        if (child.type === "inlineCode" && child.value && /[\\/]/.test(child.value) && FILE.test(child.value.replaceAll("\\", "/"))) {
          const path = resolve(child.value);
          if (path) return [{ type: "link", url: path.split("/").map(encodeURIComponent).join("/"), children: [child] }];
        }
        if (child.type === "text" && child.value) {
          const out: Node[] = [];
          let at = 0;
          for (const match of child.value.matchAll(TOKEN)) {
            const value = match[0].replace(/[.,;!?]+$/, "");
            if (!/[\\/]/.test(value) || !FILE.test(value.replaceAll("\\", "/"))) continue;
            const path = resolve(value);
            if (!path) continue;
            if (match.index > at) out.push({ type: "text", value: child.value.slice(at, match.index) });
            out.push({ type: "link", url: path.split("/").map(encodeURIComponent).join("/"), children: [{ type: "text", value }] });
            at = match.index + value.length;
          }
          if (out.length) {
            if (at < child.value.length) out.push({ type: "text", value: child.value.slice(at) });
            return out;
          }
        }
        walk(child);
        return [child];
      });
    };
    walk(tree);
  };
}
