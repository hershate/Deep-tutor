import { tokens } from "./format";
import { t } from "./index";

export const CONTEXT_COMPOSITION = "context_composition";

const PART: Record<string, string> = {
  system: "系统提示",
  tools: "工具定义",
  digest: "摘要",
  user: "用户消息",
  reply: "模型回复",
  tool_output: "工具输出",
};

interface Composition {
  total: number;
  at: number;
  active: number;
  parts: { name: string; tokens: number }[];
}

// The typed payload an over-threshold notice carries as its detail, read as the
// variables its sentence is worded from. A detail that is not one reads as none,
// so the notice keeps the kernel's English.
export function contextCompositionVars(detail: string | undefined): Record<string, string> | undefined {
  if (!detail) return undefined;
  let p: Partial<Composition>;
  try {
    p = JSON.parse(detail) as Partial<Composition>;
  } catch {
    return undefined;
  }
  if (typeof p.total !== "number" || typeof p.at !== "number" || !Array.isArray(p.parts) || p.parts.length === 0) return undefined;
  const top = p.parts
    .slice(0, 3)
    .map((x) => `${t(PART[x.name] ?? x.name)} ${tokens(x.tokens)}`)
    .join("、");
  const active = typeof p.active === "number" && p.active > 0 ? t("正在进行的这一轮占 {n}，折叠动不了它。", { n: tokens(p.active) }) : "";
  return { total: tokens(p.total), at: tokens(p.at), top, active };
}
