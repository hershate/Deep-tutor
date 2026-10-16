import { SurfaceKit } from "../SurfaceKit";
import type { ExtensionViewNode } from "../../../port/wire";
import { SurfaceView, useSurfaceRuntime } from "./view";

export default function ExtensionSurface({ body, onAction }: { body: ExtensionViewNode[]; onAction?: (id: string) => void }) {
  const runtime = useSurfaceRuntime(body);
  return runtime ? <SurfaceKit><SurfaceView runtime={runtime} onAction={onAction} /></SurfaceKit> : null;
}
