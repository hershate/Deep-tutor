import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "../../i18n";
import { useEscape } from "../dismiss";

// The native confirm box cannot exist inside an embedded webview: the dialog
// never shows, and the page waits on its own call forever. The ask renders
// here instead — a small overlay on the page that answers through a promise.

interface Ask {
  message: string;
  confirmLabel: string;
}

export function useConfirm() {
  const [ask, setAsk] = useState<Ask | null>(null);
  const answer = useRef<((ok: boolean) => void) | null>(null);

  // A page unmounted with a question open still owes its caller an answer,
  // and "never" is the one it must not give.
  useEffect(() => () => answer.current?.(false), []);

  const confirm = (message: string, confirmLabel?: string) =>
    new Promise<boolean>((resolve) => {
      answer.current?.(false);
      answer.current = resolve;
      setAsk({ message, confirmLabel: confirmLabel ?? t("确认") });
    });

  const settle = (ok: boolean) => {
    answer.current?.(ok);
    answer.current = null;
    setAsk(null);
  };

  return { confirm, overlay: ask !== null && <ConfirmAsk ask={ask} onSettle={settle} /> };
}

function ConfirmAsk({ ask, onSettle }: { ask: Ask; onSettle: (ok: boolean) => void }) {
  const refuse = useRef<HTMLButtonElement>(null);
  const take = useRef<HTMLButtonElement>(null);
  // Cancel carries the focus: a destructive question must not arm itself.
  useEffect(() => refuse.current?.focus(), []);
  useEscape(true, () => onSettle(false));

  return createPortal(
    <div
      className="tut-confirm-veil"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onSettle(false);
      }}
      onKeyDown={(ev) => {
        if (ev.key !== "Tab") return;
        ev.preventDefault();
        (document.activeElement === take.current ? refuse.current : take.current)?.focus();
      }}
    >
      <div className="tut-confirm" role="dialog" aria-modal="true" aria-label={ask.message}>
        <p>{ask.message}</p>
        <div className="tut-acts">
          <button type="button" ref={refuse} data-action="tutor.confirm-no" onClick={() => onSettle(false)}>
            {t("取消")}
          </button>
          <button type="button" ref={take} className="tut-primary" data-action="tutor.confirm-yes" onClick={() => onSettle(true)}>
            {ask.confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
