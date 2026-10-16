package agent

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/runtime/completion"
	"reasonix/internal/runtime/taskmonitor"
	"reasonix/internal/safety/evidence"
)

func writeTestFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// The ledger folds path case on Windows; elsewhere a case difference is a
// different path and must fail.
func samePath(a, b string) bool {
	if runtime.GOOS == "windows" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

type receiptFixture struct {
	a      *Agent
	root   string
	ledger *evidence.Ledger
}

func newReceiptFixture(t *testing.T) receiptFixture {
	t.Helper()
	root := testenv.TempDir(t)
	a := &Agent{}
	a.writeWorkspaceRoot = root
	a.task = taskRuntime{ledger: evidence.NewLedger()}
	return receiptFixture{a: a, root: root, ledger: a.task.ledger}
}

func (f receiptFixture) write(t *testing.T, rel string) string {
	t.Helper()
	path := filepath.Join(f.root, rel)
	writeTestFile(t, path, "x\n")
	return path
}

func (f receiptFixture) record(paths ...string) {
	rec := evidence.Receipt{
		ToolName: "bash", Success: true, Mutation: true, MutationEvidence: evidence.MutationProven, Paths: paths,
	}
	f.a.stampHostOwned(&rec)
	f.ledger.Record(rec)
}

func (f receiptFixture) report() completion.Report {
	return completion.Build(nil, f.ledger, f.a.pathInWorkspace)
}

func changedPaths(rep completion.Report) []string {
	var out []string
	for _, ch := range rep.Changes {
		out = append(out, ch.Path)
	}
	return out
}

func requirePaths(t *testing.T, got []string, want ...string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("paths = %q, want %q", got, want)
	}
	for i := range want {
		if !samePath(got[i], want[i]) {
			t.Fatalf("paths = %q, want %q", got, want)
		}
	}
}

// A turn that wrote only the host's snapshot store changed none of the work:
// nothing is listed, no mutation is counted, and no baseline is owed.
func TestTurnThatWroteOnlyTheSnapshotStoreOwesNothing(t *testing.T) {
	f := newReceiptFixture(t)
	f.record(f.write(t, filepath.Join(taskmonitor.StoreDir, "step-5", "snapshot.json")))
	rep := f.report()
	if rep.Mutations != 0 || len(rep.Changes) != 0 {
		t.Fatalf("mutations = %d, changes = %q, want none", rep.Mutations, changedPaths(rep))
	}
	if idx, ok := f.a.mutationBaseline(true); ok {
		t.Fatalf("baseline = %d, want none for a turn that kept no change", idx)
	}
}

// Writing real work beside the snapshot store keeps every obligation, and only
// the work is listed.
func TestMixedWriteKeepsTheWorkAndDropsTheSnapshotStore(t *testing.T) {
	f := newReceiptFixture(t)
	work := f.write(t, filepath.Join("src", "api.py"))
	f.record(f.write(t, filepath.Join(taskmonitor.StoreDir, "step-5", "snapshot.json")), work)
	rep := f.report()
	if rep.Mutations != 1 {
		t.Fatalf("mutations = %d, want 1", rep.Mutations)
	}
	requirePaths(t, changedPaths(rep), work)
	if _, ok := f.a.mutationBaseline(true); !ok {
		t.Fatal("a turn that wrote real work must keep its baseline")
	}
}

// A marker file anyone can write hides nothing, and an explicit write into a VCS
// store is still a change the host counts.
func TestNeitherCacheTagNorVCSStoreHidesAnExplicitWrite(t *testing.T) {
	f := newReceiptFixture(t)
	tagged := f.write(t, filepath.Join(".cache", "out.txt"))
	writeTestFile(t, filepath.Join(f.root, ".cache", "CACHEDIR.TAG"), "Signature: 8a477f597d28d172789f06886806bc55\n")
	hook := f.write(t, filepath.Join(".git", "hooks", "pre-commit"))
	f.record(tagged, hook)
	rep := f.report()
	if rep.Mutations != 1 {
		t.Fatalf("mutations = %d, want 1", rep.Mutations)
	}
	requirePaths(t, changedPaths(rep), tagged, hook)
}

// A symlink on the way to the store means the write landed somewhere else.
func TestSnapshotStoreLinkedToSourceStaysListed(t *testing.T) {
	f := newReceiptFixture(t)
	real := f.write(t, filepath.Join("src", "api.py"))
	link := filepath.Join(f.root, ".reasonix", "tasks", "x")
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Dir(real), link); err != nil {
		t.Fatalf("this runner cannot create symlinks, so the protection is untested: %v", err)
	}
	f.record(filepath.Join(link, "api.py"))
	rep := f.report()
	if rep.Mutations != 1 {
		t.Fatalf("mutations = %d, want the write through the link counted", rep.Mutations)
	}
	requirePaths(t, changedPaths(rep), filepath.Join(link, "api.py"))
}

// A worktree or submodule keeps `.git` as a file; the walk skips a VCS store
// whatever kind of entry it is.
func TestScanSkipsAVCSStoreKeptAsAFile(t *testing.T) {
	root := testenv.TempDir(t)
	writeTestFile(t, filepath.Join(root, ".git"), "gitdir: ../elsewhere\n")
	writeTestFile(t, filepath.Join(root, "a.py"), "x\n")
	scan := scanWorkspace(t.Context(), root)
	if _, held := scan.state[filepath.Join(root, ".git")]; held {
		t.Fatalf("scan recorded a VCS store file: %v", scan.state)
	}
	if _, held := scan.state[filepath.Join(root, "a.py")]; !held {
		t.Fatalf("scan lost the work file: %v", scan.state)
	}
}

func mustLink(t *testing.T, target, link string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatalf("this runner cannot create symlinks, so the protection is untested: %v", err)
	}
}

func requireListed(t *testing.T, f receiptFixture, path string) {
	t.Helper()
	rep := f.report()
	if rep.Mutations != 1 {
		t.Fatalf("mutations = %d, want the write counted", rep.Mutations)
	}
	requirePaths(t, changedPaths(rep), path)
}

// The verdict is the filesystem as the write left it: removing the link
// afterwards must not turn a counted write into a host-owned one.
func TestLinkRemovedAfterTheWriteDoesNotHideIt(t *testing.T) {
	f := newReceiptFixture(t)
	real := f.write(t, filepath.Join("src", "api.py"))
	link := filepath.Join(f.root, ".reasonix", "tasks", "x")
	mustLink(t, filepath.Dir(real), link)
	written := filepath.Join(link, "api.py")
	f.record(written)
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	requireListed(t, f, written)
}

func TestLinkedStoreRootsAndDanglingLinksStayListed(t *testing.T) {
	for name, build := range map[string]func(f receiptFixture) string{
		"the last segment is a link": func(f receiptFixture) string {
			real := f.write(t, filepath.Join("src", "api.py"))
			link := filepath.Join(f.root, ".reasonix", "tasks", "api.py")
			mustLink(t, real, link)
			return link
		},
		"the .reasonix directory is a link": func(f receiptFixture) string {
			f.write(t, filepath.Join("src", "tasks", "api.py"))
			mustLink(t, filepath.Join(f.root, "src"), filepath.Join(f.root, ".reasonix"))
			return filepath.Join(f.root, ".reasonix", "tasks", "api.py")
		},
		"a dangling link": func(f receiptFixture) string {
			link := filepath.Join(f.root, ".reasonix", "tasks", "gone")
			mustLink(t, filepath.Join(f.root, "nowhere"), link)
			return filepath.Join(link, "api.py")
		},
	} {
		t.Run(name, func(t *testing.T) {
			f := newReceiptFixture(t)
			path := build(f)
			f.record(path)
			requireListed(t, f, path)
		})
	}
}

// A path that climbs before it is cleaned is not judged by where the cleaned
// spelling lands: this one cleans into the snapshot store but was written
// through a ".." from outside it.
func TestDotDotPathIntoTheStoreIsNeverHostOwned(t *testing.T) {
	f := newReceiptFixture(t)
	f.write(t, filepath.Join("src", "keep.py"))
	sep := string(filepath.Separator)
	climbing := f.root + sep + "src" + sep + ".." + sep + taskmonitor.StoreDir + sep + "step-5" + sep + "snapshot.json"
	writeTestFile(t, filepath.Join(f.root, taskmonitor.StoreDir, "step-5", "snapshot.json"), "x\n")
	f.record(climbing)
	rep := f.report()
	if rep.Mutations != 1 || len(rep.Changes) != 1 {
		t.Fatalf("mutations = %d, changes = %q, want the write counted", rep.Mutations, changedPaths(rep))
	}
}

// The verdict is the host's at the moment the call returned. A single bash
// call that makes a link, writes through it and removes it again is not seen.
