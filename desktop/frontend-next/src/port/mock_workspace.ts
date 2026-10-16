import { HttpError } from "./http_error";
import type { ChangeDiff, GitHistory, StageRequest, WorkspaceChange, WorkspaceChanges, WorkspaceGit, WorkspaceBranches } from "./workspace";
import { MockFeedback } from "./mock_feedback";

export class MockWorkspace extends MockFeedback {
  opensFiles() { return false; }
  async openFile() {}

  // Held in a field so a switch is visible to the next read, as the kernel's endpoints keep.
  branchState: WorkspaceGit = { repo: true, name: "reasonix", branch: "main", detached: false, added: 2, removed: 1, untracked: 3 };

  async workspaceGit(): Promise<WorkspaceGit> {
    return { ...this.branchState };
  }

  // A small history with a side branch that merges back, so the graph has a
  // fork, a merge and two lanes to draw.
  async workspaceGitLog(): Promise<GitHistory> {
    const now = Math.floor(Date.now() / 1000);
    const c = (id: string, parents: string[], subject: string, mins: number, refs: string[] = [], author = "ada") =>
      ({ id, parents, subject, author, at: now - mins * 60, refs });
    return {
      repo: true, truncated: false,
      commits: [
        c("a1", ["a2"], "feat(studio): show the commit graph", 4, ["HEAD -> main", "origin/main"]),
        c("a2", ["a3", "b2"], "Merge branch 'retry-backoff'", 38),
        c("b2", ["b1"], "fix(provider): honour Retry-After", 71, ["retry-backoff"], "grace"),
        c("a3", ["a4"], "docs: describe the wire log", 95),
        c("b1", ["a4"], "test(provider): cover 429 backoff", 130, [], "grace"),
        c("a4", ["a5"], "refactor(config): split the loader", 260, ["tag: v2.38.0"]),
        c("a5", [], "chore: initial import", 1440),
      ],
    };
  }

  async branches(): Promise<WorkspaceBranches> {
    return {
      repo: this.branchState.repo,
      branches: ["main", "studio"].map((name) => ({ name, current: this.branchState.branch === name })),
    };
  }

  async switchBranch(name: string): Promise<WorkspaceGit> {
    if (!["main", "studio"].includes(name)) {
      throw new HttpError(409, "/workspace/branch/switch: no such branch", {
        code: "branch.unknown", error: "no local branch with that name",
      });
    }
    this.branchState = { ...this.branchState, branch: name, detached: false };
    return { ...this.branchState };
  }

  // The scripted transcript's tree facts, without a repository behind the fixture.
  // Held in a field so staging is visible to the next read, as the index is.
  changeList: WorkspaceChange[] = [
    { path: "internal/provider/retry.go", status: "M", unstaged: true },
    { path: "internal/config/credentials.go", status: "M", staged: true },
    { path: "internal/provider/openai/streaming/chunk_decoder.go", status: "A", staged: true, unstaged: true },
    { path: "docs/notes.md", status: "??", unstaged: true },
  ];

  async changes(): Promise<WorkspaceChanges> {
    return { repo: true, changes: this.changeList.map((c) => ({ ...c })) };
  }

  async stageChanges(req: StageRequest): Promise<void> {
    const named = new Set(req.paths ?? []);
    this.changeList = this.changeList.map((c) =>
      req.all || named.has(c.path)
        ? { ...c, staged: !req.unstage, unstaged: !!req.unstage }
        : c,
    );
  }

  // Scripted, because there is no tree to read: enough of a diff for the
  // preview to be worked on without a kernel behind the window.
  async changeDiff(path: string): Promise<ChangeDiff> {
    return {
      path,
      diff: [
        `--- a/${path}`,
        `+++ b/${path}`,
        "@@ -1,6 +1,7 @@",
        " package permission",
        " ",
        "-func allows(cmd string) bool {",
        "-	return strings.Contains(cmd, \"rm -rf\")",
        "+func allows(cmd string) bool {",
        "+	ast, err := shellparse.Parse(cmd)",
        "+	if err != nil {",
        "+		return false",
        "+	}",
        " }",
      ].join("\n"),
      truncated: false,
    };
  }

}
