import type { Tool } from "../../port/wire";
import type { Item } from "../../state/session_types";
import { parseSurface, validSurfaceValue } from "./spec";
import type { SurfaceSpec } from "./spec";

export function surfaceOfTool(tool: Tool): SurfaceSpec | null {
  if (tool.err || tool.resolvedName && tool.resolvedName !== "render_surface") return null;
  try {
    const args = JSON.parse(tool.args ?? "");
    if (tool.name === "render_surface") return parseSurface(args);
    if (tool.name !== "use_capability" || typeof args?.action !== "string" || args.action.trim().toLowerCase() !== "call") return null;
    if (tool.resolvedName !== "render_surface" && (typeof args.capability_id !== "string" || args.capability_id.trim() !== "tool:render_surface")) return null;
    return parseSurface(args.arguments);
  } catch { return null; }
}

export function surfaceFields(spec: SurfaceSpec) {
  const fields: SurfaceSpec["body"] = [];
  const walk = (nodes: SurfaceSpec["body"]) => nodes.forEach(n => { if (n.field) fields.push(n); if (n.children) walk(n.children); });
  walk(spec.body);
  return fields;
}

export function surfaceActions(spec: SurfaceSpec) {
  const actions: SurfaceSpec["body"] = [];
  const walk = (nodes: SurfaceSpec["body"]) => nodes.forEach(n=>{if(n.kind==="button")actions.push(n);if(n.children)walk(n.children);});
  walk(spec.body);return actions;
}

export function actionSurfaceOfTool(tool: Tool) {
  const spec=surfaceOfTool(tool);
  if(!spec || !surfaceActions(spec).length)return null;
  try {return JSON.parse(tool.output ?? "{}").nonblocking===true ? spec : null;} catch {return null;}
}

export function visibleSurfaceOfTool(tool: Tool) {
  const action = actionSurfaceOfTool(tool);
  if (action) return action;
  const spec = surfaceOfTool(tool);
  if (!spec || tool.output === undefined || surfaceFields(spec).length || surfaceActions(spec).length) return null;
  const dashboard = (nodes: SurfaceSpec["body"]): boolean => nodes.some(n => n.kind === "dashboard" || !!n.children && dashboard(n.children));
  return dashboard(spec.body) ? spec : null;
}

function recordedValues(spec: SurfaceSpec, output?: string): Record<string, unknown> | undefined {
  try {
    const raw = JSON.parse(output ?? "");
    if (typeof raw.surface_id !== "string" || !raw.values || typeof raw.values !== "object" || Array.isArray(raw.values)) return;
    const fields = surfaceFields(spec);
    if (Object.keys(raw.values).some(key => !fields.some(n => n.key === key))) return;
    for (const n of fields) {
      const value = Object.hasOwn(raw.values, n.key!) ? raw.values[n.key!] : undefined;
      if (value === undefined ? n.field!.required : !validSurfaceValue(n.field!, value)) return;
    }
    return raw.values;
  } catch { return; }
}

export function surfaceHistory(item: Item): Item {
  if (item.t !== "tool") return item;
  const spec = surfaceOfTool({ ...item.tool, err: undefined });
  if (!spec || surfaceActions(spec).length || !surfaceFields(spec).length) return item;
  const values = item.tool.err ? undefined : recordedValues(spec, item.tool.output);
  return { t: "ask", id: item.id, ask: { id: item.tool.id ?? item.id, questions: [{ id: "surface", prompt: spec.title ?? "", options: [], surface: spec }] }, answered: values ? [[]] : [], surfaceValues: values, surfaceExpired: !values };
}
