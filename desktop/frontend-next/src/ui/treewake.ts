const WAKE_WINDOW_MS = 1_000;
const WAKE_SETTLE_MS = 100;

export function watchTreeWake(refresh: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let next = 0;
  const wake = () => {
    if (document.visibilityState !== "visible" || timer !== undefined) return;
    timer = setTimeout(() => {
      timer = undefined;
      next = Date.now() + WAKE_WINDOW_MS;
      refresh();
    }, Math.max(WAKE_SETTLE_MS, next - Date.now()));
  };
  window.addEventListener("focus", wake);
  window.addEventListener("online", wake);
  window.addEventListener("pageshow", wake);
  document.addEventListener("visibilitychange", wake);
  return () => {
    clearTimeout(timer);
    window.removeEventListener("focus", wake);
    window.removeEventListener("online", wake);
    window.removeEventListener("pageshow", wake);
    document.removeEventListener("visibilitychange", wake);
  };
}
