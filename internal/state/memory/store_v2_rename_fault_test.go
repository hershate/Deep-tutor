package memory

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/fileutil"
	"reasonix/internal/base/testenv"
)

// withFault installs the package seam for one test. It is process-wide, so
// tests that use it must not run in parallel.
func withFault(t *testing.T, fn func(op, path string) error) {
	t.Helper()
	previous := memoryFault
	memoryFault = fn
	t.Cleanup(func() { memoryFault = previous })
}

// failRenames fails the next rename of a matching path, the way a real
// filesystem fault would.
func failRenames(t *testing.T, match func(path string) bool) {
	t.Helper()
	withFault(t, func(op, path string) error {
		if op == "rename" && match(path) {
			return &os.PathError{Op: "rename", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
}

// failIndexPublish fails publishing MEMORY.md into dir, wherever that happens.
func failIndexPublish(t *testing.T, dir string) {
	t.Helper()
	indexPath := filepath.Join(dir, indexFile)
	withFault(t, func(op, path string) error {
		if op == "read-index" && path == indexPath {
			return nil
		}
		if path == indexPath && (op == "rename" || op == "publish-index") {
			return &os.PathError{Op: "rename", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
}

func stopFailing(dir string) {
	memoryFault = nil
	fileutil.CrashPoint = nil
	_ = os.Chmod(dir, 0o755)
}

func namesInListForID(store Store, id string) []string {
	var names []string
	for _, m := range store.List() {
		if m.ID == id {
			names = append(names, m.Name)
		}
	}
	return names
}

func TestStoreV2RenameKeepsOneActiveIdentityWhenOldFileCleanupFails(t *testing.T) {
	store := Store{Dir: testenv.TempDir(t)}
	first, err := store.SaveWithOptions(Memory{Name: "alpha", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	failRenames(t, func(path string) bool { return strings.HasSuffix(path, "alpha.md") })

	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Description: "renamed", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	if saveErr == nil {
		t.Fatal("the rename reported success although the old name could not be removed")
	}
	stopFailing(store.Dir)

	oldFact, oldOK := store.Read("alpha")
	newFact, newOK := store.Read("beta")
	byID, _ := store.Read(first.Memory.ID)
	if oldOK && newOK {
		t.Fatalf("one id became two active facts after %v: Read(alpha)=rev%d %q / Read(beta)=rev%d %q / Read(id)=rev%d %q; List() names for %s = %v; MEMORY.md =\n%s",
			saveErr, oldFact.Revision, oldFact.Body, newFact.Revision, newFact.Body,
			byID.Revision, byID.Body, first.Memory.ID, namesInListForID(store, first.Memory.ID),
			mustReadString(t, filepath.Join(store.Dir, indexFile)))
	}
}

// TestStoreV2RenameLeavesNoDanglingIndexEntry: after a rename whose index flush
// fails, the index must not keep naming the removed file.
func TestStoreV2RenameLeavesNoDanglingIndexEntry(t *testing.T) {
	store := Store{Dir: testenv.TempDir(t)}
	first, err := store.SaveWithOptions(Memory{Name: "alpha", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	indexPath := filepath.Join(store.Dir, indexFile)
	failIndexPublish(t, store.Dir)

	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Description: "renamed", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	if saveErr == nil {
		t.Fatal("the rename reported success although the index could not be rewritten")
	}
	stopFailing(store.Dir)

	index := mustReadString(t, indexPath)
	_, oldOK := store.Read("alpha")
	active, newOK := store.Read(first.Memory.ID)
	if strings.Contains(index, "alpha.md") && !oldOK && newOK {
		t.Fatalf("after %v the index still names the removed file: active=%+v; Read(alpha)=%v; MEMORY.md =\n%s", saveErr, active, oldOK, index)
	}
}

// TestStoreV2RetriedRenameLeavesOneActiveName: a retry after the reported
// failure converges on one active identity.
func TestStoreV2RetriedRenameLeavesOneActiveName(t *testing.T) {
	store := Store{Dir: testenv.TempDir(t)}
	first, err := store.SaveWithOptions(Memory{Name: "alpha", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	failRenames(t, func(path string) bool { return strings.HasSuffix(path, "alpha.md") })

	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Description: "renamed", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	if saveErr == nil {
		t.Fatal("the rename reported success although the old name could not be removed")
	}
	stopFailing(store.Dir)

	retried, retryErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Description: "renamed", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	if retryErr != nil {
		t.Fatalf("the failed rename left the fact unretryable: retry = %v (first failure was %v)", retryErr, saveErr)
	}
	names := namesInListForID(store, first.Memory.ID)
	if len(names) != 1 || names[0] != "beta" {
		t.Fatalf("the retry returned revision %d but the id still has active names %v; MEMORY.md =\n%s",
			retried.Memory.Revision, names, mustReadString(t, filepath.Join(store.Dir, indexFile)))
	}
}

func TestStoreV2CrossScopeMoveKeepsTheIndexInStep(t *testing.T) {
	root := testenv.TempDir(t)
	store := Store{Dir: filepath.Join(root, "project"), GlobalDir: filepath.Join(root, "global")}
	if err := os.MkdirAll(store.GlobalDir, 0o755); err != nil {
		t.Fatal(err)
	}
	globalIndex := filepath.Join(store.GlobalDir, indexFile)
	if err := os.WriteFile(globalIndex, []byte("# Memory\n\nHand-written note.\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	first, err := store.SaveWithOptions(Memory{Name: "project/alpha.md", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	// Fail the publication of the staged file, so every step before it has
	// already succeeded and the rollback is the interesting part.
	failRenames(t, func(path string) bool { return strings.HasPrefix(path, ".staged-") })

	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Scope: FactScopeGlobal, Description: "moved", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	stopFailing(store.GlobalDir)

	projectIndex := mustReadString(t, filepath.Join(store.Dir, indexFile))
	global := mustReadString(t, globalIndex)
	_, oldActive := store.Read("project/alpha.md")
	_, newActive := store.Read("global/beta.md")
	oldListed := strings.Contains(projectIndex, "alpha.md")
	newListed := strings.Contains(global, "beta.md")

	switch {
	case oldActive && !newActive && oldListed && !newListed:
		// Fully back on the old identity: the move failed and changed nothing.
		if saveErr == nil {
			t.Fatal("the move reported success but left only the old identity")
		}
	case newActive && !oldActive && newListed && !oldListed:
		// Fully committed on the new identity.
		if saveErr != nil {
			t.Fatalf("the move completed as the new identity but reported %v", saveErr)
		}
	default:
		t.Fatalf("the move left a state that is neither the old nor the new identity: err=%v oldActive=%v newActive=%v oldListed=%v newListed=%v\nproject MEMORY.md=\n%s\nglobal MEMORY.md=\n%s",
			saveErr, oldActive, newActive, oldListed, newListed, projectIndex, global)
	}
}
func TestStoreV2FailedWriteAfterSnapshotKeepsTheStoreConsistent(t *testing.T) {
	root := testenv.TempDir(t)
	store := Store{Dir: filepath.Join(root, "project"), GlobalDir: filepath.Join(root, "global")}
	first, err := store.SaveWithOptions(Memory{Name: "alpha", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(store.GlobalDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(store.GlobalDir, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(store.GlobalDir, 0o755) })

	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Scope: FactScopeGlobal, Description: "moved", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	if saveErr == nil {
		t.Skip("this user can write into a 0555 directory; the write fault cannot be injected")
	}
	if got, ok := store.Read("project/alpha.md"); !ok || got.Body != "v1" || got.Revision != 1 {
		t.Fatalf("failed write changed the old identity: %+v, ok=%v (save error %v)", got, ok, saveErr)
	}
	if later, ok := store.Read("global/beta.md"); ok {
		t.Fatalf("failed write left a second active file: %+v (save error %v)", later, saveErr)
	}
}

// Once the new name is published and indexed the save is committed: failing to
// delete the hidden retired copy must not be reported as a failed save.
func TestStoreV2CommittedRenameSurvivesRetiredCopyCleanupFailure(t *testing.T) {
	store := Store{Dir: testenv.TempDir(t)}
	first, err := store.SaveWithOptions(Memory{Name: "alpha", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	withFault(t, func(op, path string) error {
		if op == "remove" {
			return &os.PathError{Op: "remove", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Description: "renamed", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	stopFailing(store.Dir)
	if saveErr != nil {
		t.Fatalf("a committed rename reported failure because the retired copy could not be deleted: %v", saveErr)
	}
	active, ok := store.Read(first.Memory.ID)
	if !ok || active.Name != "beta" || active.Body != "v2" {
		t.Fatalf("the committed rename is not readable as the new fact: %+v ok=%v", active, ok)
	}
	if index := mustReadString(t, filepath.Join(store.Dir, indexFile)); !strings.Contains(index, "beta.md") {
		t.Fatalf("the committed rename is missing from the index:\n%s", index)
	}
	// The retired copy must really still be there, and it must not be visible as
	// an active fact to any reader.
	retired, err := filepath.Glob(filepath.Join(store.Dir, ".retired-*"))
	if err != nil {
		t.Fatal(err)
	}
	if len(retired) == 0 {
		t.Fatal("the injection removed nothing, so the cleanup failure was never exercised")
	}
	for _, name := range namesInListForID(store, first.Memory.ID) {
		if strings.HasPrefix(name, ".retired-") {
			t.Fatalf("a retired copy is visible as an active fact: %q", name)
		}
	}
	if _, ok := store.Read("alpha"); ok {
		t.Fatal("the retired name is still readable as an active fact")
	}
	if index := mustReadString(t, filepath.Join(store.Dir, indexFile)); strings.Contains(index, ".retired-") {
		t.Fatalf("the index lists a retired copy:\n%s", index)
	}
}

// When the new index cannot be published and the published file cannot be
// withdrawn either, the save must still end as one identity: finishing forwards
// is preferred over leaving the new file live but unindexed.
func TestStoreV2CommittedMoveFinishesForwardsWhenWithdrawalFails(t *testing.T) {
	root := testenv.TempDir(t)
	store := Store{Dir: filepath.Join(root, "project"), GlobalDir: filepath.Join(root, "global")}
	if err := os.MkdirAll(store.GlobalDir, 0o755); err != nil {
		t.Fatal(err)
	}
	first, err := store.SaveWithOptions(Memory{Name: "project/alpha.md", Description: "first", Body: "v1"}, SaveOptions{})
	if err != nil {
		t.Fatal(err)
	}
	globalIndex := filepath.Join(store.GlobalDir, indexFile)
	failures := 0
	withFault(t, func(op, path string) error {
		if op == "publish-index" && path == globalIndex && failures == 0 {
			failures++
			return &os.PathError{Op: "rename", Path: path, Err: os.ErrPermission}
		}
		if op == "remove" && strings.HasPrefix(path, "beta.md") {
			return &os.PathError{Op: "remove", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
	_, saveErr := store.SaveWithOptions(Memory{
		ID: first.Memory.ID, Name: "beta", Scope: FactScopeGlobal, Description: "moved", Body: "v2",
	}, SaveOptions{ExpectedRevision: 1, RequireExpectedRevision: true})
	stopFailing(store.GlobalDir)

	_, oldActive := store.Read("project/alpha.md")
	active, newActive := store.Read("global/beta.md")
	if !newActive || oldActive {
		t.Fatalf("the move did not end as the new identity: err=%v new=%+v oldActive=%v", saveErr, active, oldActive)
	}
	if saveErr != nil {
		t.Fatalf("the move finished forwards but reported failure: %v", saveErr)
	}
	if index := mustReadString(t, globalIndex); !strings.Contains(index, "beta.md") {
		t.Fatalf("the finished move is absent from the target index:\n%s", index)
	}
}
