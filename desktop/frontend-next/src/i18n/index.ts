// The interface's language. It is not the language the model answers in —
// that follows what you wrote, per message, and is decided in the kernel.
// Conflating the two would mean picking English here silenced Chinese replies.
export type Lang = "zh" | "en";

// Chinese source text is the key. Inventing names for 3800 fragments would cost
// more than it returns, and this way the call site still reads as the sentence
// it renders — a translation that is missing falls back to the original rather
// than to a blank or a key. The catalogue's completeness is a test's job, the
// same way internal/base/i18n does it for the CLI.
let lang: Lang = "zh";
let dict: Record<string, string> = {};
// The catalogue of the one language that needs one, kept after its first fetch
// so a later boot() can put it back without another round trip. A real window
// boots once; a test suite reboots per case, and the setup file preloads this.
let catalogue: Record<string, string> | null = null;

export const STORAGE = "rx-lang";

/** boot fixes the language for the session, before the first paint. It reads
 *  the local choice rather than waiting on the kernel, because a round trip
 *  here would show one language and then swap it.
 *
 *  Fixed for the session on purpose: switching reloads the window, which is
 *  what keeps every memoised component from having to subscribe to a language
 *  it will be asked about once in its life. */
export function boot(): Lang {
  lang = resolve(localStorage.getItem(STORAGE));
  // Identity until load() fills in the session's catalogue: a switch back to
  // Chinese must not keep drawing the English that came before it.
  dict = lang === "en" && catalogue ? catalogue : {};
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  return lang;
}

/** load brings in the catalogue a window that draws English needs. The
 *  catalogue is most of the i18n chunk, and a Chinese window — the default —
 *  never opens it; an English window fetches it beside the kernel probe,
 *  before the first render, so nothing draws in one language and swaps.
 *  Chinese is the key itself and needs no fetch. Switching language reloads
 *  the window (see adopt), so there is no live switch this could miss. */
export function load(fixed: Lang): Promise<void> {
  if (fixed !== "en") return Promise.resolve();
  if (catalogue) {
    dict = catalogue;
    return Promise.resolve();
  }
  return import("./en").then((module) => {
    catalogue = module.EN;
    dict = catalogue;
  });
}

/** preload fetches the English catalogue without changing what draws now.
 *  For the test setup, which cannot await inside the synchronous boot(): the
 *  catalogue lands in the memo, and the next boot() that resolves English
 *  adopts it with no round trip — while a file that never boots keeps drawing
 *  the identity it started with. */
export function preload(): Promise<void> {
  if (catalogue) return Promise.resolve();
  return import("./en").then((module) => {
    catalogue = module.EN;
  });
}

/** resolve turns a stored or configured value — including "auto" and null —
 *  into the language to actually draw in. Anything but an explicit "zh" or
 *  "en" follows the machine. */
export function resolve(pref: string | null | undefined): Lang {
  const want = (pref ?? "").trim().toLowerCase();
  if (want === "zh" || want === "en") return want;
  return machine();
}

/** machine reads what the computer is set to. Everything Chinese lands on zh —
 *  simplified, traditional, Hong Kong, Singapore, and the Cantonese and
 *  Mandarin tags a system may report instead — because this interface has one
 *  Chinese, not two. Everything else is English: that is the fallback, not a
 *  claim that the machine is English.
 *
 *  Only the first preference counts. A machine set to French with Chinese
 *  second is a French machine, and the answer for it is English. */
function machine(): Lang {
  const first = navigator.languages?.[0] ?? navigator.language ?? "";
  return isChinese(first) ? "zh" : "en";
}

// Matched on the tag rather than on a list of regions, so a locale nobody
// thought of (zh-Hans-SG, cmn-Hans-CN) still lands where it belongs.
function isChinese(tag: string): boolean {
  const s = tag.toLowerCase();
  return s.startsWith("zh") || s.startsWith("yue") || s.startsWith("cmn") || s.includes("hans") || s.includes("hant");
}

export const current = (): Lang => lang;

/** adopt takes the language the kernel has on file and makes the window agree
 *  with it. The local copy is what boots — a round trip would show one language
 *  and then swap it — so a machine that has never opened this window (a fresh
 *  install, a cleared cache) would otherwise ignore the choice already saved in
 *  the config. Reloading is how a running tree changes language at all.
 *
 *  Returns true while it is reloading, so the caller knows this window is on
 *  its way out. */
export function adopt(configured: string | null | undefined): boolean {
  const pref = (configured ?? "").trim().toLowerCase();
  const stored = localStorage.getItem(STORAGE);
  if (stored !== null && stored === pref) return false;
  // "" is a real answer — follow the machine — and is stored as one, or every
  // launch keeps asking the kernel the same question.
  localStorage.setItem(STORAGE, pref);
  if (resolve(pref) === lang) return false;
  location.reload();
  return true;
}

/** t translates, with {name} placeholders filled from vars. */
export function t(zh: string, vars?: Record<string, string | number>): string {
  const out = dict[zh] ?? zh;
  if (!vars) return out;
  return out.replace(/\{(\w+)\}/g, (whole, key: string) => (key in vars ? String(vars[key]) : whole));
}

/** plural picks between two forms. English needs it and Chinese does not, so
 *  the Chinese side simply ignores the distinction rather than carrying a
 *  second copy of every counted string. */
export function plural(n: number, one: string, many: string, vars?: Record<string, string | number>): string {
  return t(n === 1 ? one : many, { n, ...vars });
}

