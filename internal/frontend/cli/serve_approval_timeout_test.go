package cli

import (
	"testing"

	"deep-tutor/internal/frontend/serve"
)

// The serve command answers over the network, so its sessions must not wait
// for a decision for good: a browser that closes takes the answer with it.
// The terminal frontends keep the unbounded wait, which is right for a person
// sitting at the prompt.
func TestServeBuildBoundsTheApprovalWait(t *testing.T) {
	overrides := serveBuildOverrides("v1.20.0", nil, nil)
	if overrides.ApprovalTimeout != serve.ApprovalTimeout {
		t.Fatalf("approval timeout = %v, want %v", overrides.ApprovalTimeout, serve.ApprovalTimeout)
	}
	opts := cliProfileBuildOptions("model", 0, false, nil, "", overrides)
	if opts.ApprovalTimeout != serve.ApprovalTimeout {
		t.Fatalf("built approval timeout = %v, want %v", opts.ApprovalTimeout, serve.ApprovalTimeout)
	}
}
