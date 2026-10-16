import { useState } from "react";
import type { Item } from "../state/session";
import { useReceiptState } from "../state/foldpref";
import { ReceiptCard, ReceiptHint } from "./cards/ReceiptCard";

export function ReceiptSlot({ r }: { r: Extract<Item, { t: "receipt" }>["r"] }) {
  const state = useReceiptState();
  const shown = state === "on";
  const [open, setOpen] = useState(false);
  if (state === "unknown") return null;
  if (shown || open) return <><ReceiptCard r={r} />{!shown && <ReceiptHint r={r} open onToggle={() => setOpen(false)} />}</>;
  return <ReceiptHint r={r} open={false} onToggle={() => setOpen(true)} />;
}
