const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const keys = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const text = (v: unknown, cap = 200) => typeof v === "string" && new TextEncoder().encode(v).length <= cap;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function richOption(o: Record<string, unknown>): boolean {
  if (o.image !== undefined) {
    const i = o.image;
    if (!object(i) || !keys(i, ["path", "alt", "source"]) || !text(i.path, 1024) || !text(i.alt) || !i.alt) return false;
    if (i.source !== undefined && !imageSourceURL(i.source)) return false;
    const path = i.path as string;
    if (/[:\\?#%\x00\r\n\t]/.test(path) || path.split("/").some(s => !s || s === "." || s === "..") || !/\.(png|jpe?g|webp|avif|gif)$/i.test(path)) return false;
  }
  if (o.group !== undefined && !text(o.group)) return false;
  if (o.currency !== undefined && !text(o.currency, 8)) return false;
  if (o.price !== undefined && (!finite(o.price) || o.price < 0 || !o.currency)) return false;
  if (o.location !== undefined) {
    const p = o.location;
    if (!object(p) || !keys(p, ["latitude", "longitude"]) || !finite(p.latitude) || !finite(p.longitude) || Math.abs(p.latitude) > 85 || Math.abs(p.longitude) > 180) return false;
  }
  if (o.facts !== undefined) {
    if (!Array.isArray(o.facts) || o.facts.length > 12) return false;
    const seen = new Set<string>();
    for (const f of o.facts) {
      if (!object(f) || !keys(f, ["label", "value"]) || !text(f.label) || !f.label || !text(f.value, 4000) || seen.has(f.label as string)) return false;
      seen.add(f.label as string);
    }
  }
  return true;
}

export function richDataset(raw: unknown): boolean {
  if (!object(raw) || !keys(raw, ["categories", "series"]) || !Array.isArray(raw.categories) || !Array.isArray(raw.series)) return false;
  const categories = raw.categories;
  if (!categories.length || categories.length > 64 || new Set(categories).size !== categories.length || categories.some(c => !text(c) || !c)) return false;
  if (!raw.series.length || raw.series.length > 8) return false;
  const seen = new Set<string>();
  for (const s of raw.series) {
    if (!object(s) || !keys(s, ["id", "label", "unit", "values"]) || typeof s.id !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(s.id) || seen.has(s.id) || !text(s.label) || !s.label || (s.unit !== undefined && !text(s.unit)) || !Array.isArray(s.values) || s.values.length !== categories.length || s.values.some(v => !finite(v))) return false;
    seen.add(s.id);
  }
  return true;
}

export function imageSourceURL(source: unknown): URL | null {
  if (!text(source, 4096) || !/^https?:\/\//.test(source as string) || /[\\\r\n\t]/.test(source as string)) return null;
  try {
    const url = new URL(source as string);
    return url.hostname && !url.username && !url.password ? url : null;
  } catch { return null; }
}
