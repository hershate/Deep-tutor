import type { ChangeDiff, StageRequest, WorkspaceBranches, WorkspaceChanges, WorkspaceGit } from "./port";
import type { GitHistory } from "./workspace";
import { SseFeedback } from "./sse_feedback";
import { host } from "./host";
import { HttpError } from "./http_error";

// The workspace's tree facts: what it differs by and which branch git says it
// is on. The reads answer from the tree, so a frontend re-asks rather than
// deriving either from tool events.
export class SseWorkspace extends SseFeedback {
  opensFiles() { return host().opensFiles(); }

  async openFile(path: string) {
    const why = await host().openFile(this.base, path);
    if (why) throw new HttpError(0, why.error || "not opened", why, !!(why.code || why.error));
  }

  changes() {
    return this.get<WorkspaceChanges>("/changes");
  }

  stageChanges(req: StageRequest) {
    return this.post("/changes/stage", req);
  }

  workspaceGit() {
    return this.get<WorkspaceGit>("/workspace/git");
  }

  workspaceGitLog(limit?: number) {
    return this.get<GitHistory>("/workspace/git/log" + (limit ? `?limit=${limit}` : ""));
  }

  branches() {
    return this.get<WorkspaceBranches>("/workspace/branches");
  }

  switchBranch(name: string) {
    return this.post0<WorkspaceGit>("/workspace/branch/switch", { name });
  }

  changeDiff(path: string) {
    return this.get<ChangeDiff>(`/changes/diff?path=${encodeURIComponent(path)}`);
  }
}
