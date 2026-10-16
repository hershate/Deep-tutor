import { t } from ".";

export const JOB_FINISHED = "job_finished";
export const JOB_KILLED = "job_killed";
export const JOB_FAILED = "job_failed";

// The typed payload a background-job notice carries as its detail, read as the
// variables its sentence is worded from. Anything that does not parse reads as no
// payload, so the notice keeps the kernel's own text.
export function jobNoticeVars(detail: string | undefined): { name: string } | undefined {
  if (!detail) return undefined;
  try {
    const p = JSON.parse(detail) as { id?: unknown; label?: unknown };
    if (typeof p.id !== "string" || p.id === "") return undefined;
    return { name: typeof p.label === "string" && p.label !== "" ? p.label : p.id };
  } catch {
    return undefined;
  }
}

// How a failed process ended, worded from the typed fields the producer wrote:
// a signal first, since an exit code is then only the shell's echo of it. The
// job function's own error text stands in when the producer did not type it.
export function jobErrorText(detail: string | undefined): string | undefined {
  if (!detail) return undefined;
  try {
    const p = JSON.parse(detail) as { error?: unknown; exitCode?: unknown; signal?: unknown };
    if (typeof p.signal === "number" && p.signal > 0) return t("被信号 {signal} 终止", { signal: p.signal });
    if (typeof p.exitCode === "number") return t("退出码 {code}", { code: p.exitCode });
    return typeof p.error === "string" && p.error !== "" ? p.error : undefined;
  } catch {
    return undefined;
  }
}
