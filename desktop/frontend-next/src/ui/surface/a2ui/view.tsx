import { useEffect, useEffectEvent, useState } from "react";
import { A2uiSurface } from "@a2ui/react/v0_9";
import type { ExtensionViewNode } from "../../../port/wire";
import { createSurfaceRuntime, type SurfaceRuntime } from "./runtime";
import { SurfaceContext } from "./catalog";

export function useSurfaceRuntime(body: ExtensionViewNode[], restored?: Record<string, unknown>) {
  const [runtime, setRuntime] = useState<SurfaceRuntime>();
  const encoded = JSON.stringify(body);
  const initialize = useEffectEvent((nodes: ExtensionViewNode[]) => createSurfaceRuntime(nodes, restored));
  useEffect(() => {
    const next = initialize(JSON.parse(encoded));
    setRuntime(next);
    return () => next.dispose();
  }, [encoded]);
  useEffect(() => { if (runtime && restored) runtime.setAnswers(restored); }, [runtime, restored]);
  return runtime;
}

export function SurfaceView({ runtime, disabled = true, inputs = false, actionDisabled, onAction }: { runtime: SurfaceRuntime; disabled?: boolean; inputs?: boolean; actionDisabled?: boolean; onAction?: (id: string) => void }) {
  return <SurfaceContext.Provider value={{ disabled, inputs, actionDisabled, onAction }}><div className="xview"><A2uiSurface surface={runtime.surface} /></div></SurfaceContext.Provider>;
}
