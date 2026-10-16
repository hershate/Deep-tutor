import type { ExtensionSurface } from "./wire";

// GET /extensions/surfaces: the surfaces a session had, and whether the kernel
// can say that is all of them. "dropped" means a surface frame was refused after
// the session's log reached its retention cap; a log that merely reached the cap
// says nothing.
export interface ExtensionSurfaceReplay {
  surfaces: ExtensionSurface[];
  incomplete?: "dropped";
}
