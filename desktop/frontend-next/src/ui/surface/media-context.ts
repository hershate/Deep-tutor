import { createContext } from "react";
import type { AgentPort } from "../../port/port";

export type SurfaceMediaPort = Pick<AgentPort, "workspaceImageURL">;
export const SurfaceMediaContext = createContext<Partial<SurfaceMediaPort> | undefined>(undefined);
