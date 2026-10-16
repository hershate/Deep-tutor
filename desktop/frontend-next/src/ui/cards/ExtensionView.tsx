import type { ExtensionViewNode } from "../../port/wire";
import { lazySurface } from "../surface/a2ui/lazy";

const LibraryView = lazySurface(() => import("../surface/a2ui/extension"));

export function ExtensionView({ body, onAction }: { body: ExtensionViewNode[]; onAction?: (id: string) => void }) {
  return <LibraryView body={body} onAction={onAction} />;
}
