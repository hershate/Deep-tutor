import { SseHttp } from "./sse_http";
import { base64Of, guardUpload, tooLarge, UPLOAD_LIMIT_MB } from "./upload";
import type { Appearance } from "./port";

// The appearance half of the port. Chained the way the fixture's faces are, so
// SsePort satisfies AgentPort in one declaration and each subject keeps its own
// file.
export class SseLook extends SseHttp {
  appearance() {
    return this.get<Appearance>("/appearance");
  }
  saveAppearance(look: Appearance) {
    return this.post0<Appearance>("/appearance", {
      language: look.language ?? "",
      zoom: look.zoom ?? 0,
      readSize: look.readSize ?? 0,
      fontUi: look.fontUi ?? "",
      fontMono: look.fontMono ?? "",
      width: look.width ?? "",
      opacity: look.wallpaper?.opacity ?? 0,
      dim: look.wallpaper?.dim ?? 0,
      focusX: look.wallpaper?.focusX ?? 0.5,
      focusY: look.wallpaper?.focusY ?? 0.5,
    });
  }

  // JSON, not raw bytes: csrfGuard admits nothing else, which is what stops a
  // cross-site form from posting here at all.
  // JSON, not raw bytes: csrfGuard admits nothing else, which is what stops a
  // cross-site form from posting here at all.
  async uploadWallpaper(blob: Blob) {
    guardUpload(blob.size, UPLOAD_LIMIT_MB.wallpaper, tooLarge("wallpaper.too_large", "limit"));
    const res = await fetch(this.base + "/appearance/wallpaper", {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ mime: blob.type, data: await base64Of(blob) }),
    });
    const body = (await res.json().catch(() => ({}))) as Appearance & { error?: string };
    if (!res.ok) throw new Error(body.error || `/appearance/wallpaper: ${res.status}`);
    return body;
  }
  async clearWallpaper() {
    const res = await fetch(this.base + "/appearance/wallpaper", {
      method: "DELETE",
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`/appearance/wallpaper: ${res.status}`);
  }
}
