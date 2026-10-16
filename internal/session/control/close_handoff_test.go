package control

import (
	"context"
	"io"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/state/sessionstore"
	"reasonix/internal/state/workspacelease"
	"reasonix/internal/tools/jobs"
)

func TestCloseHandingOffJobsKeepsJobsAndDefersCleanupUntilTheyFinish(t *testing.T) {
	m := jobs.NewManager(event.Discard)
	t.Cleanup(m.Close)
	var cleaned atomic.Bool
	old := New(Options{Sink: event.Discard, Jobs: m, Cleanup: func() { cleaned.Store(true) }})
	next := New(Options{Sink: event.Discard, Jobs: m})
	if !next.SharesJobsWith(old) {
		t.Fatal("controllers built on one manager must report sharing it")
	}
	job := m.Start("task", "long", func(ctx context.Context, _ io.Writer) (string, error) {
		<-ctx.Done()
		return "", ctx.Err()
	})

	old.CloseHandingOffJobs()
	time.Sleep(100 * time.Millisecond)
	if len(m.Running()) != 1 {
		t.Fatal("handing off cancelled the job")
	}
	if cleaned.Load() {
		t.Fatal("cleanup ran while a job still used what it releases")
	}

	if !m.Kill(job.ID) {
		t.Fatal("job not killable")
	}
	deadline := time.Now().Add(5 * time.Second)
	for !cleaned.Load() {
		if time.Now().After(deadline) {
			t.Fatal("cleanup never ran after the job finished")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestCloseHandingOffJobsCleansUpAtOnceWhenNothingRuns(t *testing.T) {
	m := jobs.NewManager(event.Discard)
	t.Cleanup(m.Close)
	var cleaned atomic.Bool
	old := New(Options{Sink: event.Discard, Jobs: m, Cleanup: func() { cleaned.Store(true) }})
	old.CloseHandingOffJobs()
	if !cleaned.Load() {
		t.Fatal("cleanup waited on a job table with nothing in it")
	}
}

func borrowedFixture(t *testing.T) (m *jobs.Manager, lease *workspacelease.Owner, prev *Controller, prevPath, nextPath string) {
	t.Helper()
	dir := testenv.TempDir(t)
	m = jobs.NewManager(event.Discard)
	t.Cleanup(m.Close)
	lease, err := workspacelease.New(dir, filepath.Join(dir, "lease"), nil)
	if err != nil {
		t.Fatal(err)
	}
	prevPath, nextPath = filepath.Join(dir, "prev.jsonl"), filepath.Join(dir, "next.jsonl")
	prev = New(Options{Sink: event.Discard, Jobs: m, WorkspaceLease: lease, SessionPath: prevPath, WorkspaceRoot: dir})
	return m, lease, prev, prevPath, nextPath
}

// A replacement that only borrowed the manager and lease binds nothing on them
// until its switch is decided, so discarding it leaves them as they were.
func TestBorrowedJobsStayWithTheOriginalUntilTakeOver(t *testing.T) {
	m, lease, prev, prevPath, nextPath := borrowedFixture(t)
	dir := filepath.Dir(prevPath)
	adopter := New(Options{Sink: event.Discard, Jobs: m, WorkspaceLease: lease, SessionPath: nextPath, WorkspaceRoot: dir})
	m.Start("task", "long", func(ctx context.Context, _ io.Writer) (string, error) {
		<-ctx.Done()
		return "", ctx.Err()
	})

	if !m.ObservedBy(prev) || m.ObservedBy(adopter) {
		t.Fatal("a borrower became the completion observer before the switch was decided")
	}
	if got, want := lease.SessionID(), sessionstore.BranchID(prevPath); got != want {
		t.Fatalf("lease session id = %q, want the original's %q", got, want)
	}

	adopter.CloseSharingJobs()
	if !m.ObservedBy(prev) || lease.SessionID() != sessionstore.BranchID(prevPath) {
		t.Fatal("discarding the replacement changed what the manager and lease answer to")
	}
	if len(m.Running()) != 1 {
		t.Fatal("discarding the replacement cancelled the original's job")
	}

	winner := New(Options{Sink: event.Discard, Jobs: m, WorkspaceLease: lease, SessionPath: nextPath, WorkspaceRoot: dir})
	winner.TakeOverJobs()
	if !m.ObservedBy(winner) || lease.SessionID() != sessionstore.BranchID(nextPath) {
		t.Fatal("TakeOverJobs did not move the observer and the lease identity to the new owner")
	}
}
