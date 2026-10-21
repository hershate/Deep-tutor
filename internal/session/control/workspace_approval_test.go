package control

import (
	"testing"

	"deep-tutor/internal/contract/config"
)

// approveWorkspace approves what dir's configuration names, standing in for a
// person who ran `deep-tutor trust` there. Tests of the gate itself never call it.
func approveWorkspace(t *testing.T, dir string) {
	t.Helper()
	_, _ = config.Roots{}.ApproveWorkspacePrograms(dir)
}
