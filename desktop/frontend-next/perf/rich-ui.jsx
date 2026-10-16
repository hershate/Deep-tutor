import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { SurfaceCard, surfaceTool } from "../src/ui/surface/SurfaceCard";
import { scenarios } from "./rich-ui-data";
import "../src/styles/tokens.css";
import "../src/styles/app.css";
import "../src/styles/studio.css";
import "../src/styles/themes.css";

const params = new URLSearchParams(location.search);
const selected = params.get("scenario") || "map";
document.documentElement.dataset.theme = params.get("theme") || "light";
document.body.style.overflow = "auto";

function Preview() {
  const spec = scenarios[selected] || scenarios.map;
  const [saved, setSaved] = useState();
  const item = { t: "ask", id: "preview", ask: { id: "preview", questions: [{ id: "surface", prompt: "Preview", options: [], surface: spec }] }, ...(saved ? { answered: [[]], surfaceValues: saved } : {}) };
  return <main style={{ maxWidth: 1160, margin: "24px auto", padding: "0 20px 32px" }}>
    <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 20, marginBottom: 24 }}><div><strong style={{ fontSize: 20 }}>Reasonix</strong><div style={{ color: "var(--muted)", fontSize: 12 }}>交互式任务界面 · 开发验收 / 示例数据与虚构商品示意图</div></div><nav style={{ display: "flex", gap: 16 }}>{[["map", "地图与行程"], ["comparison", "产品对比"], ["dashboard", "数据仪表盘"]].map(([id, label]) => <a key={id} href={`?scenario=${id}&theme=${params.get("theme") || "light"}`} style={{ color: id === selected ? "var(--text)" : "var(--muted)", fontSize: 13 }}>{label}</a>)}</nav></header>
    {selected === "dashboard" ? surfaceTool({ name: "render_surface", readOnly: true, args: JSON.stringify(spec), output: "Displayed" }, false) : <SurfaceCard port={{ workspaceImageURL: path => `/${path}` }} item={item} onAnswer={async (_item, _ask, answers) => { if (answers[0]) { window.previewAnswer = answers[0].value; setSaved(answers[0].value); } }} />}
  </main>;
}
createRoot(document.getElementById("root")).render(<Preview />);
