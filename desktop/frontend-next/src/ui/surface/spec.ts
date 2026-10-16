import { richOption, richDataset } from "./rich-spec";
import type { ExtensionViewNode, SurfaceField } from "../../port/wire";

export interface SurfaceSpec {
  spec_version: 1;
  title?: string;
  body: ExtensionViewNode[];
}

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const text = (v: unknown, cap: number, required = false) => v === undefined ? !required : typeof v === "string" && new TextEncoder().encode(v).length <= cap && (!required || v.length > 0);
const id = (v: unknown): v is string => typeof v === "string" && /^[a-zA-Z0-9_-]{1,64}$/.test(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function validSurfaceValue(f: SurfaceField, value: unknown): boolean {
  switch (f.type) {
    case "string": return typeof value === "string" && Array.from(value).length <= (f.max_length || 4000) && (!f.required || value.length > 0);
    case "number": return finite(value) && (f.min === undefined || value >= f.min) && (f.max === undefined || value <= f.max);
    case "bool": return typeof value === "boolean";
    case "choice": return typeof value === "string" && !!f.options?.some(o => o.value === value);
    case "multichoice": return Array.isArray(value) && value.length <= (f.options?.length ?? 0) && (!f.required || value.length > 0) && new Set(value).size === value.length && value.every(v => typeof v === "string" && f.options?.some(o => o.value === v));
  }
}

function field(raw: unknown): raw is SurfaceField {
  if (!object(raw) || !keys(raw, ["type", "required", "default", "min", "max", "max_length", "options"])) return false;
  if (typeof raw.type !== "string" || !["string", "number", "bool", "choice", "multichoice"].includes(raw.type)) return false;
  if (raw.required !== undefined && typeof raw.required !== "boolean") return false;
  for (const k of ["min", "max"]) if (raw[k] !== undefined && (raw.type !== "number" || !finite(raw[k]))) return false;
  if (finite(raw.min) && finite(raw.max) && raw.min > raw.max) return false;
  if (raw.max_length !== undefined && (raw.type !== "string" || !Number.isInteger(raw.max_length) || Number(raw.max_length) < 0 || Number(raw.max_length) > 4000)) return false;
  const choice = raw.type === "choice" || raw.type === "multichoice";
  if (raw.options !== undefined && !Array.isArray(raw.options)) return false;
  const options = raw.options as unknown[] | undefined;
  if (choice ? !options?.length : options !== undefined) return false;
  if (options && options.length > 16) return false;
  const seen = new Set<string>();
  for (const o of options ?? []) {
    if (!object(o) || !keys(o, ["value", "label", "description", "preview", "location", "group", "facts", "price", "currency", "image"]) || !id(o.value) || seen.has(o.value) || !text(o.label, 200, true) || !text(o.description, 4000) || !text(o.preview, 4000)) return false;
    if (!richOption(o)) return false;
    seen.add(o.value);
  }
  return raw.default === undefined || validSurfaceValue(raw as unknown as SurfaceField, raw.default);
}

export function parseSurface(raw: unknown): SurfaceSpec | null {
  if (!object(raw) || !keys(raw, ["spec_version", "title", "body"]) || raw.spec_version !== 1 || !text(raw.title, 200) || !Array.isArray(raw.body) || !raw.body.length) return null;
  if (new TextEncoder().encode(JSON.stringify(raw)).length > 65536) return null;
  let remaining = 200;
  const fields = new Set<string>();
  const actions = new Set<string>();
  function node(v: unknown, depth: number): boolean {
    if (!object(v) || depth > 8 || --remaining < 0) return false;
    if (!text(v.value, 4000) || !text(v.key, 200) || !text(v.label, 200)) return false;
    if (v.tone !== undefined && (typeof v.tone !== "string" || !["", "dim", "strong", "ok", "warn", "err", "accent"].includes(v.tone))) return false;
    let allowed: string[];
    switch (v.kind) {
      case "text": case "markdown": if (!text(v.value, 4000, true)) return false; allowed = ["value", "tone"]; break;
      case "kv": if (!text(v.key, 200, true)) return false; allowed = ["key", "value", "tone"]; break;
      case "pip": if (!v.tone) return false; allowed = ["value", "tone"]; break;
      case "meter": if (!finite(v.progress) || v.progress < 0 || v.progress > 1) return false; allowed = ["label", "progress", "tone"]; break;
      case "button":
        if (!id(v.actionId) || actions.has(v.actionId) || actions.size >=16 || !text(v.label,200,true)) return false;
        actions.add(v.actionId); allowed = ["label","actionId"]; break;
      case "divider": allowed = []; break;
      case "row": case "stack": if (!Array.isArray(v.children) || !v.children.length) return false; allowed = ["children"]; break;
      case "diff_preview":
        if (!object(v.diff) || !keys(v.diff, ["before", "after"]) || typeof v.diff.before !== "string" || typeof v.diff.after !== "string" || !text(v.diff.before, 4000) || !text(v.diff.after, 4000)) return false;
        allowed = ["label", "diff"]; break;
      case "dashboard":
        if (!richDataset(v.dataset)) return false; allowed = ["label", "dataset"]; break;
      case "field": case "choice_card": case "editable_text": case "map": case "comparison":
        if (!id(v.key) || fields.has(v.key) || fields.size >= 16 || !text(v.label, 200, true) || !field(v.field)) return false;
        if (v.kind === "choice_card" && v.field.type !== "choice" && v.field.type !== "multichoice") return false;
        if (v.kind === "editable_text" && v.field.type !== "string") return false;
        if (v.kind === "map" && (v.field.type !== "multichoice" || !v.field.options?.every(o => o.location))) return false;
        if (v.kind === "comparison" && v.field.type !== "choice" && v.field.type !== "multichoice") return false;
        fields.add(v.key); allowed = ["key", "label", "field"]; break;
      default: return false;
    }
    if (!keys(v, ["kind", ...allowed])) return false;
    return !Array.isArray(v.children) || v.children.every(child => node(child, depth + 1));
  }
  return raw.body.every(n => node(n, 1)) ? raw as unknown as SurfaceSpec : null;
}
