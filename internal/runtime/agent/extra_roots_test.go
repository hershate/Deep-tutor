package agent

import (
	"os"
	"path/filepath"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/safety/evidence"
)

func TestAGrantedRootIsTheWorkspaceForWhatOwesVerification(t *testing.T) {
	f := newReceiptFixture(t)
	extra, outside := testenv.TempDir(t), testenv.TempDir(t)
	inExtra := filepath.Join(extra, "out.txt")
	inOutside := filepath.Join(outside, "scratch.txt")

	f.ledger.Record(evidence.Receipt{ToolName: "bash", Success: true, Mutation: true, MutationEvidence: evidence.MutationProven, Paths: []string{inExtra}})
	writeTestFile(t, inExtra, "x\n")
	if f.a.pathInWorkspace(inExtra) {
		t.Fatal("without a grant the folder is outside the workspace")
	}
	if _, owed := f.a.mutationBaseline(true); owed {
		t.Fatal("without a grant a write outside the root owes nothing")
	}
	f.a.extraRoots = []string{extra}
	if !f.a.pathInWorkspace(inExtra) {
		t.Fatal("a granted root is part of the work product")
	}
	if f.a.pathInWorkspace(inOutside) {
		t.Fatal("an ungranted folder became workspace")
	}

	if _, owed := f.a.mutationBaseline(true); !owed {
		t.Fatal("a turn that only wrote the granted root owed no verification")
	}
}

func TestTheScanSeesAChangeInAGrantedRoot(t *testing.T) {
	a := &Agent{}
	a.observeRoot = testenv.TempDir(t)
	extra := testenv.TempDir(t)
	a.extraRoots = []string{extra}
	before := a.scanObserved(t.Context())
	if !before.complete {
		t.Fatal("scan incomplete")
	}
	if err := os.WriteFile(filepath.Join(extra, "built.o"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	changed, ok := before.changed(a.scanObserved(t.Context()))
	if !ok || len(changed) != 1 || filepath.Base(changed[0]) != "built.o" {
		t.Fatalf("changed = %v, ok = %v; a write in the granted root went unobserved", changed, ok)
	}
}

func TestAChildThatOnlyWroteAGrantedRootIsNotJudgedToHaveChangedNothing(t *testing.T) {
	a := &Agent{}
	a.observeRoot = testenv.TempDir(t)
	extra := testenv.TempDir(t)
	a.extraRoots = []string{extra}
	plan := &toolCallPlan{scanBefore: a.scanObserved(t.Context())}
	if err := os.WriteFile(filepath.Join(extra, "built.o"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	rec := evidence.Receipt{ToolName: "bash", Success: true, Mutation: true, MutationEvidence: evidence.MutationUnknown, Command: "make"}
	a.settleUnchangedWorkspace(t.Context(), &rec, plan)
	if !rec.Mutation || rec.MutationEvidence != evidence.MutationProven || len(rec.Paths) != 1 {
		t.Fatalf("a write in the granted root was settled as %+v", rec)
	}

}

func TestTheScanLimitIsATotalAcrossTheRoots(t *testing.T) {
	a := &Agent{}
	a.workspaceScanLimit = 10
	a.observeRoot = testenv.TempDir(t)
	extra := testenv.TempDir(t)
	a.extraRoots = []string{extra}
	for i := range 6 {
		for _, dir := range []string{a.observeRoot, extra} {
			if err := os.WriteFile(filepath.Join(dir, "f"+string(rune('a'+i))), []byte("x"), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	scan := a.scanObserved(t.Context())
	if scan.complete || !scan.overLimit {
		t.Fatalf("12 files across two roots under a limit of 10 read as complete=%v over=%v", scan.complete, scan.overLimit)
	}
}
