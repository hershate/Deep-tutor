package serve

import (
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/session/control"
)

// A served session that waits for a browser forever parks its controller for
// good when the browser closes. The bound is what keeps a server meant for
// many people from filling up with sessions nobody will ever answer.
func TestServedSessionsBoundTheirApprovalWait(t *testing.T) {
	if ApprovalTimeout <= 0 {
		t.Fatalf("ApprovalTimeout = %v, want a positive bound", ApprovalTimeout)
	}
	ctrl := control.New(control.Options{Sink: event.Discard, SessionDir: testenv.TempDir(t)})
	t.Cleanup(ctrl.Close)
	srv := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	srv.SetApprovalTimeout(ApprovalTimeout)
	if got := srv.rebuildOptions(nil, "model").ApprovalTimeout; got != ApprovalTimeout {
		t.Fatalf("rebuild approval timeout = %v, want %v", got, ApprovalTimeout)
	}
}

// A pane the hub opens is reached over the network too, so it carries the
// same bound as the pane it replaces.
func TestHubCarriesTheApprovalTimeoutToItsPanes(t *testing.T) {
	h := NewHub(HubOptions{ApprovalTimeout: ApprovalTimeout})
	opts := h.openOptions(testenv.TempDir(t), "model", event.Discard)
	if opts.ApprovalTimeout != ApprovalTimeout {
		t.Fatalf("hub approval timeout = %v, want %v", opts.ApprovalTimeout, ApprovalTimeout)
	}
}

// A host whose decision channel is a terminal waits for as long as the person
// is willing to. The bound belongs to the networked surface, not to the
// kernel: a host that asks for none gets none.
func TestAHubWithoutTheTimeoutWaitsForThePerson(t *testing.T) {
	h := NewHub(HubOptions{})
	if got := h.openOptions(testenv.TempDir(t), "model", event.Discard).ApprovalTimeout; got != 0 {
		t.Fatalf("approval timeout = %v, want none", got)
	}
}
