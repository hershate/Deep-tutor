export const NoticeCodeSessionTreeChanged = "session_tree_changed";

import { openLiveStream } from "./livestream";

export function watchTree(onChanged: () => void): () => void {
  let seen: number | null = null;
  return openLiveStream(
    () => "/tree/events" + (seen === null ? "" : `?lastEventId=${seen}`),
    (raw) => {
      let frame: { kind?: string; code?: string; seq?: number };
      try { frame = JSON.parse(raw); } catch { return; }
      if (!Number.isSafeInteger(frame.seq) || frame.seq! < 0) return;
      if (frame.kind !== "stream_watermark" && frame.kind !== "stream_gap" && frame.code !== NoticeCodeSessionTreeChanged) return;
      if (seen === frame.seq) return;
      seen = frame.seq!;
      onChanged();
    },
  );
}
