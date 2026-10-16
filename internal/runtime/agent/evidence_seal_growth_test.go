package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/runtime/completion"
	"reasonix/internal/runtime/taskcontract"
	"reasonix/internal/safety/evidence"
	"reasonix/internal/state/observation"
	"reasonix/internal/state/trustedstate"
)

func storeBytes(t *testing.T, root string) int64 {
	t.Helper()
	var total int64
	err := filepath.WalkDir(root, func(_ string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		info, err := d.Info()
		if err == nil {
			total += info.Size()
		}
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return total
}

// A workspace with thousands of captured tests puts them in the contract
// revision and nowhere else: a turn that changes no criterion seals a bundle
// that names the revision and adds nothing proportional to the criteria.
func TestSealedBundleDoesNotCopyTheContractEachTurn(t *testing.T) {
	root := t.TempDir()
	store := trustedstate.Open(root, nil)
	a, sink := contractAgent(t, &scriptedProvider{}, "", store)
	a.deliveryProfile = true
	a.task.baselineCriteria = map[string]evidence.TestCriterion{}
	for i := range 3000 {
		path := fmt.Sprintf("/work/tree/benchmarks/e2e/tasks/case-%04d/workdir/pkg/order_test.go", i)
		a.task.baselineCriteria[path] = evidence.TestCriterion{LogicalID: path, Digest: evidence.DigestOf([]byte(path))}
	}
	seal := func(input string) {
		a.sealShadowBundle(input, taskcontract.New(input), completion.Report{}, nil, false)
	}

	seal("first")
	first := storeBytes(t, root)
	seal("second")
	added := storeBytes(t, root) - first

	rec, err := store.Record(trustedstate.Digest(sealedRecord(t, sink.last(t))))
	if err != nil {
		t.Fatal(err)
	}
	if len(rec.Refs) != 1 {
		t.Fatalf("record refs = %v, want the obligations object so Verify covers it", rec.Refs)
	}
	payload, err := store.Object(rec.Payload)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload) > 16<<10 {
		t.Fatalf("bundle payload is %d bytes for 3000 criteria; it must name the contract, not copy it", len(payload))
	}
	if strings.Contains(string(payload), "order_test.go") {
		t.Fatal("bundle repeats criterion identities already held by the contract revision")
	}
	if added > 32<<10 {
		t.Fatalf("a turn that changed no criterion added %d bytes to the store", added)
	}
}

// A chain written before obligations moved out of the payload keeps verifying
// and keeps serving the snapshot the next turn compares against.
func TestLegacyBundleStillServesTheSnapshotInAMixedChain(t *testing.T) {
	store := trustedstate.Open(t.TempDir(), nil)
	legacy, err := json.Marshal(map[string]any{
		"kind":           legacyShadowBundleKind,
		"verdict":        map[string]any{"outcome": "completed", "obligations": []any{}},
		"snapshot_after": observation.Snapshot{},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := store.Append(context.Background(), "ws", legacyShadowBundleKind, legacy); err != nil {
		t.Fatal(err)
	}
	if _, ok := previousSnapshot(&EvidenceSeal{Store: store, Stream: "ws"}); !ok {
		t.Fatal("the legacy bundle's snapshot was not found")
	}

	a, _ := contractAgent(t, &scriptedProvider{}, "", store)
	a.sealShadowBundle("next", taskcontract.New("next"), completion.Report{}, []evidence.Receipt{{ToolName: "write_file", Success: true, Write: true, Mutation: true}}, false)
	if _, err := store.Verify("ws"); err != nil {
		t.Fatalf("mixed chain must verify: %v", err)
	}
}

// A store that cannot take the obligations object seals nothing and says why.
func TestSealIsAuditedWhenTheObligationsCannotBeStored(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "objects"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	store := trustedstate.Open(root, nil)
	a, sink := contractAgent(t, &scriptedProvider{}, "", store)
	a.sealShadowBundle("x", taskcontract.New("x"), completion.Report{}, []evidence.Receipt{{ToolName: "write_file", Success: true, Write: true, Mutation: true}}, false)
	got := sink.last(t)
	if got.Sealed || got.FailureCode != "trusted_state.unwritable" {
		t.Fatalf("audit = %+v, want unsealed with the store's typed cause", got)
	}
}
