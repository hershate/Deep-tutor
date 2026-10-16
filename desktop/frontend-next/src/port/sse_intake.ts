import { HttpError, type Attachment, type DroppedRef } from "./port";
import { SseHttp } from "./sse_http";
import { SseTheme } from "./sse_theme";
import { attachmentLimitMb, base64Of, guardUpload, tooLarge } from "./upload";

// Where a file enters a turn: bytes the page holds, or paths the shell knows.
export class SseIntake extends SseTheme {
  // JSON, not raw bytes: csrfGuard admits nothing else, and that guard is what
  // stops a cross-site form posting here at all.
  async attach(blob: Blob, name?: string) {
    guardUpload(blob.size, attachmentLimitMb(blob.type, name), tooLarge("attachment.too_large", "limit_mb"));
    let data: string;
    try {
      data = await base64Of(blob);
    } catch (e) {
      const detail = String(e);
      throw new HttpError(0, detail, { code: "attachment.unreadable", error: detail, params: { detail } });
    }
    const res = await fetch(this.base + "/attachments", {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ mime: blob.type, name: name ?? "", data }),
    });
    if (!res.ok) await SseHttp.fail("/attachments", res);
    return (await res.json()) as Attachment;
  }

  dropRefs(paths: string[]) {
    return this.post0<DroppedRef[]>("/drop", { paths });
  }
}
