package control

import (
	"context"
	"os"
	"path/filepath"
	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/checkpoint"
	"reasonix/internal/state/sessionstore"
	"testing"
)

func TestAvailableUndoAndChangeEvents(t *testing.T) {
	root, dir := testenv.TempDir(t), testenv.TempDir(t)
	path := filepath.Join(root, "note.txt")
	if err := os.WriteFile(path, []byte("before"), 0644); err != nil {
		t.Fatal(err)
	}
	sess := sessionstore.NewSession("sys")
	ag := agent.New(nil, tool.NewRegistry(), sess, agent.Options{}, event.Discard)
	changes := make(chan event.Event, 8)
	c := New(Options{Runner: ag, Executor: ag, SessionDir: dir,
		SessionPath: filepath.Join(dir, "test.jsonl"), WorkspaceRoot: root,
		Sink: event.FuncSink(func(e event.Event) {
			if e.Kind == event.RewindChanged {
				changes <- e
			}
		}),
	})
	t.Cleanup(c.Close)
	c.beginCheckpoint(context.Background(), "edit")
	c.mutationObserver.BeforeMutation("note.txt", "write_file", checkpoint.CaptureBeforeMutation)
	if err := os.WriteFile(path, []byte("after"), 0644); err != nil {
		t.Fatal(err)
	}
	c.mutationObserver.AfterMutation("note.txt", "write_file")
	plan, err := c.PrepareRewind(0, RewindCode)
	if err != nil {
		t.Fatal(err)
	}
	result, err := c.CommitRewind(plan.PlanID)
	if err != nil || !result.OK {
		t.Fatalf("commit: %+v, %v", result, err)
	}
	select {
	case e := <-changes:
		if e.Text != "" {
			t.Fatalf("event has content: %+v", e)
		}
	default:
		t.Fatal("commit did not emit change")
	}
	if err := c.beginRotation(); err != nil {
		t.Fatal(err)
	}
	offer, err := c.AvailableUndo()
	c.endRotation()
	if err != nil || offer == nil || offer.TransactionID != result.TransactionID {
		t.Fatalf("rotation read: %+v, %v", offer, err)
	}
	b := c.checkpoints.storeRef().Barrier()
	if err := b.EnterWrite(); err != nil {
		t.Fatal(err)
	}
	offer, err = c.AvailableUndo()
	b.ExitWrite()
	if err != nil || offer != nil {
		t.Fatalf("writer read: %+v, %v", offer, err)
	}
	c.beginCheckpoint(context.Background(), "next turn")
	select {
	case <-changes:
	default:
		t.Fatal("new turn did not emit invalidation")
	}
	offer, err = c.AvailableUndo()
	if err != nil || offer != nil {
		t.Fatalf("invalidated read: %+v, %v", offer, err)
	}
}
