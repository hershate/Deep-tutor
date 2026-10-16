import { useState } from "react";
import type { AgentPort } from "../port/port";
import { t } from "../i18n";
import "../styles/workspace-image.css";

export const isWorkspaceImage = (path: string) => /\.(png|jpe?g|gif|webp|avif|bmp|ico|svg)$/i.test(path);

export function WorkspaceImage({ port, path }: { port: AgentPort; path: string }) {
  const [state, setState] = useState<"loading" | "loaded" | "failed">("loading");
  return <>
    <div className="workbench-filebar"><strong title={path}>{path}</strong></div>
    <div className="workbench-image" aria-busy={state === "loading"}>
      {state === "loading" && <div className="workbench-empty">{t("正在读取…")}</div>}
      {state === "failed" ? <div className="workbench-error" role="alert">{t("图片加载失败，它可能已被移动、删除或超过大小限制")}</div>
        : <img src={port.workspaceImageURL(path)} alt={path} hidden={state !== "loaded"} onLoad={() => setState("loaded")} onError={() => setState("failed")} />}
    </div>
  </>;
}
