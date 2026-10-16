import type { Queued } from "./port";

export interface SurfaceAction {
  surfaceId: string;
  sessionId: string;
  callId: string;
  actionId: string;
  requestId: string;
  values: Record<string, unknown>;
}
export interface SurfaceActionRequest {
  action: SurfaceAction;
  delivery: "queue" | "steer";
}
export interface SurfaceActionRecord {
  action: SurfaceAction;
  itemId?: string;
  status?: string;
  canWithdraw?: boolean;
  active?: boolean;
}
export interface SurfaceActionPort {
  surfaceAction(request: SurfaceActionRequest): Promise<Queued>;
  surfaceActionState(callId: string): Promise<SurfaceActionRecord>;
  cancelQueued(itemId: string): Promise<void>;
}
