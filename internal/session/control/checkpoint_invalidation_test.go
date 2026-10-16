package control

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/checkpoint"
	"reasonix/internal/state/sessionstore"
)

func controllerWithRetainedUndo(t *testing.T, runErr error) (*Controller, *checkpointEventRunner, <-chan event.Event, string, string) {
	t.Helper()
	root, dir := testenv.TempDir(t), testenv.TempDir(t)
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("before"), 0o644); err != nil {
		t.Fatal(err)
	}
	sess := sessionstore.NewSession("sys")
	runner := &checkpointEventRunner{session: sess, err: runErr, started: make(chan struct{})}
	executor := agent.New(nil, tool.NewRegistry(), sess, agent.Options{}, event.Discard)
	events := make(chan event.Event, 8)
	sessionPath := filepath.Join(dir, "session.jsonl")
	c := New(Options{
		Runner: runner, Executor: executor, SessionDir: dir, SessionPath: sessionPath, WorkspaceRoot: root,
		Sink: event.FuncSink(func(e event.Event) {
			if e.Kind == event.TurnDone {
				events <- e
			}
		}),
	})
	t.Cleanup(c.Close)
	if err := c.beginCheckpoint(context.Background(), "edit"); err != nil {
		t.Fatal(err)
	}
	c.mutationObserver.BeforeMutation("note.txt", "write_file", checkpoint.CaptureBeforeMutation)
	if err := os.WriteFile(path, []byte("after"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := c.mutationObserver.AfterMutation("note.txt", "write_file"); err != nil {
		t.Fatal(err)
	}
	plan, err := c.PrepareRewind(0, RewindCode)
	if err != nil {
		t.Fatal(err)
	}
	result, err := c.CommitRewind(plan.PlanID)
	if err != nil || !result.OK {
		t.Fatalf("rewind: %+v, %v", result, err)
	}
	return c, runner, events, ckptDir(sessionPath), result.TransactionID
}

func TestTurnReportsUndoInvalidationFailureBeforeRunnerStarts(t *testing.T) {
	c, runner, events, dir, previous := controllerWithRetainedUndo(t, nil)
	transactions := filepath.Join(dir, "transactions")
	if err := os.Rename(transactions, transactions+"-backup"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transactions, []byte("blocked"), 0o644); err != nil {
		t.Fatal(err)
	}
	before := runner.session.Len()
	c.Send("next turn")
	done := receiveCheckpointTurnDone(t, events)
	if done.Err == nil || !strings.Contains(done.Err.Error(), "persist undo invalidation") || !strings.Contains(done.Err.Error(), previous) {
		t.Fatalf("completion did not carry the storage error: %v", done.Err)
	}
	select {
	case <-runner.started:
		t.Fatal("runner started despite rejected undo invalidation")
	default:
	}
	if runner.session.Len() != before {
		t.Fatal("rejected turn changed model history")
	}
	store := c.checkpoints.storeRef()
	if store.NextTurn() != 1 {
		t.Fatalf("rejected turn consumed checkpoint: %d", store.NextTurn())
	}
	if store.LastUndoTransactionID() != previous {
		t.Fatal("failed invalidation discarded the old undo")
	}
}

func TestFailedNewTurnDoesNotRevivePreviousUndo(t *testing.T) {
	failure := errors.New("simulated model failure")
	c, runner, _, dir, previous := controllerWithRetainedUndo(t, failure)
	if err := c.RunTurn(context.Background(), "next turn"); !errors.Is(err, failure) {
		t.Fatalf("turn error: %v", err)
	}
	select {
	case <-runner.started:
	default:
		t.Fatal("the new turn never reached the runner")
	}
	if offer, err := c.AvailableUndo(); err != nil || offer != nil {
		t.Fatalf("undo after failed turn: %+v, %v", offer, err)
	}
	reloaded := checkpoint.New(dir, c.WorkspaceRoot())
	if offer, err := reloaded.AvailableUndo(); err != nil || offer != nil {
		t.Fatalf("obsolete undo recovered: %+v, %v", offer, err)
	}
	if _, err := reloaded.UndoRewind(previous, nil); err == nil {
		t.Fatal("previous undo accepted after a new turn")
	}
}
