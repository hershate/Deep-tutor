package evidence

import "testing"

// HostOwned names files by the same identity as Paths, or the two never meet.
func TestRecordNormalizesHostOwnedLikePaths(t *testing.T) {
	l := NewLedger()
	raw := `C:\Work\.reasonix\.\tasks\..\tasks\Snap.json`
	l.Record(Receipt{ToolName: "bash", Success: true, Mutation: true, Paths: []string{raw}, HostOwned: []string{raw}})
	got := l.Receipts()[0]
	if len(got.Paths) != 1 || len(got.HostOwned) != 1 || got.Paths[0] != got.HostOwned[0] {
		t.Fatalf("Paths = %q, HostOwned = %q, want one identical identity", got.Paths, got.HostOwned)
	}
}
