import { useEffect } from "react";
import { createPortal } from "react-dom";
import { t } from "../i18n";
import { pinToViewport } from "./place";

export type SelectionMenu = { text: string; x: number; y: number; anchor: HTMLElement };

/** The right-click menu over text selected in an open workspace file: it is the
 *  way to bring a passage, not the whole file, into the conversation. */
export function WorkbenchSelectionMenu({ menu, onQuote, onClose }: { menu: SelectionMenu; onQuote: (text: string) => void; onClose: () => void }) {
  useEffect(() => {
    const shut = () => onClose();
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape" && e.key !== "Tab") return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); }
      menu.anchor.focus({ preventScroll: true });
      onClose();
    };
    addEventListener("click", shut);
    addEventListener("keydown", key, true);
    addEventListener("blur", shut);
    return () => {
      removeEventListener("click", shut);
      removeEventListener("keydown", key, true);
      removeEventListener("blur", shut);
    };
  }, [menu, onClose]);
  return createPortal(
    <div
      className="tabmenu"
      role="menu"
      aria-label={t("选中的内容")}
      data-action-keydown="layer.dismiss"
      ref={(el) => { if (el) pinToViewport(el, menu.x, menu.y); }}
    >
      <button role="menuitem" autoFocus data-action="workbench.quote-selection" onClick={() => { onQuote(menu.text); menu.anchor.focus({ preventScroll: true }); onClose(); }}>
        {t("加入对话")}
      </button>
    </div>, document.body,
  );
}
