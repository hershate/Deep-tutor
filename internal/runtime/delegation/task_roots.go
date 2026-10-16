package delegation

import (
	"slices"

	"reasonix/internal/runtime/agent"
)

// WithExtraRoots names the granted folders that count as the workspace for what
// a child's scan observes, the same list the executor reads.
func (t *TaskTool) WithExtraRoots(roots []string) *TaskTool {
	t.extraRoots = slices.Clone(roots)
	return t
}

func (t *TaskTool) withExtraRoots(opts agent.Options) agent.Options {
	opts.ExtraWorkspaceRoots = t.extraRoots
	return opts
}
