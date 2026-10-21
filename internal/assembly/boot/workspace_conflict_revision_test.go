package boot

import (
	"encoding/json"
	"strings"
	"testing"

	"deep-tutor/internal/contract/eventwire"
	"deep-tutor/internal/state/workspacelease"
)

func TestWorkspaceWaitContractDoesNotClaimTheWorkspaceIsFree(t *testing.T) {
	e := workspaceLeaseNotice(workspacelease.Wait{Outcome: workspacelease.WaitAcquired})
	if strings.Contains(e.Text, "workspace is free") {
		t.Fatalf("path acquisition advertised workspace availability: %s", e.Text)
	}
	data, err := json.Marshal(eventwire.ToWire(e))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "workspaceLease") {
		t.Fatalf("notice has no typed claim scope: %s", data)
	}
}
