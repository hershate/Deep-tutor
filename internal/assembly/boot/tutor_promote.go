package boot

import (
	"slices"

	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/tutor"
)

// addTutorPromote registers promote_artifact when both a tutor store
// directory and a workspace resolve: drafts live in the workspace and the
// promotion lands in the tutor library, so either missing leaves the tool out.
// An explicit cfg.Tools.Enabled list that omits the tool keeps it out, the
// same gating the built-ins honour.
func addTutorPromote(reg *tool.Registry, enabled []string, dir, workDir string) {
	if dir == "" || workDir == "" {
		return
	}
	if len(enabled) > 0 && !slices.Contains(enabled, "promote_artifact") {
		return
	}
	reg.Add(tutor.NewPromoteArtifactTool(dir, workDir))
}
