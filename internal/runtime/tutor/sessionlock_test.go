package tutor

import (
	"errors"
	"testing"
	"time"
)

// sessionlock_test.go — the session's lifecycle lock.
//
// The store has no transaction, so the delete of a session and the writes
// that can still land on it have to be serialised by hand. Without that, a
// delete that lands between the finalizer's "is this run still mine" check
// and its write is undone by the write: the session a person deleted comes
// back, with a plan under it.
//
// The lock is what makes the delete authoritative. These tests pin the three
// paths that touch a session's files — the delete, the run registration and
// the finalizer — to the one lock.

// blockingLLM parks every call until it is released, so a test can decide
// exactly when the run reaches its finalizer.
type blockingLLM struct {
	inner   *fakeLLM
	base    func(string, int) string
	release chan struct{}
	entered chan struct{}
}

func newBlockingLLM(reply func(system string, call int) string) *blockingLLM {
	b := &blockingLLM{
		inner:   &fakeLLM{},
		base:    reply,
		release: make(chan struct{}),
		entered: make(chan struct{}, 1),
	}
	b.inner.reply = b.wrap()
	return b
}

// wrap answers through the blocking gate.
func (b *blockingLLM) wrap() func(string, int) string {
	return func(system string, call int) string {
		select {
		case b.entered <- struct{}{}:
		default:
		}
		<-b.release
		return b.base(system, call)
	}
}

// waitEntered blocks until the run has reached the model.
func (b *blockingLLM) waitEntered(t *testing.T) {
	t.Helper()
	select {
	case <-b.entered:
	case <-time.After(10 * time.Second):
		t.Fatal("the run never reached the model")
	}
}

// newBlockingService builds a service whose only model waits for the test.
func newBlockingService(t *testing.T) (*Service, *blockingLLM) {
	t.Helper()
	llm := newBlockingLLM(happyReply)
	svc := NewService(openTestStore(t), ServiceOptions{
		Provider: func() (LLM, error) { return llm.inner, nil },
		Searcher: fakeSearcher{},
	})
	return svc, llm
}

// The finalizer waits for the session's lock. A delete in flight holds it
// while the files are removed, so a finalizer that ran concurrently would
// write the session back after the delete — the session would come back.
func TestFinalizerWaitsForTheSessionsLock(t *testing.T) {
	svc, llm := newBlockingService(t)
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.StartGenerate(sess.ID, "两周学会 React", IntentArgs{DailyMinutes: 60}); err != nil {
		t.Fatal(err)
	}
	ch, stop, err := svc.Subscribe(sess.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	llm.waitEntered(t)

	// Hold the session's lock the way a delete in flight holds it.
	unlock := svc.Store().LockSession(sess.ID)
	close(llm.release)

	// The run finishes, but its finalizer cannot write while the lock is
	// held, so no terminal event reaches the client.
	deadline := time.After(400 * time.Millisecond)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				t.Fatal("the stream closed while the finalizer was blocked")
			}
			if ev.Type == EventDone || ev.Type == EventError || ev.Type == EventClarifyNeeded {
				t.Fatalf("the finalizer wrote while the session's lock was held: %s", ev.Type)
			}
		case <-deadline:
			goto released
		}
	}
released:
	unlock()

	// With the lock gone the finalizer finishes and the client sees it.
	terminal := time.After(10 * time.Second)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				t.Fatal("the stream closed before the terminal event")
			}
			switch ev.Type {
			case EventDone, EventError, EventClarifyNeeded:
				return
			}
		case <-terminal:
			t.Fatal("the finalizer never finished after the lock was released")
		}
	}
}

// The delete waits for the session's lock, so it cannot remove the files
// while a write is landing on them.
func TestDeleteWaitsForTheSessionsLock(t *testing.T) {
	svc, _ := newBlockingService(t)
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	unlock := svc.Store().LockSession(sess.ID)
	deleted := make(chan error, 1)
	go func() { deleted <- svc.DeleteSession(sess.ID) }()
	select {
	case err := <-deleted:
		t.Fatalf("the delete did not wait for the session's lock: %v", err)
	case <-time.After(300 * time.Millisecond):
	}
	unlock()
	select {
	case err := <-deleted:
		if err != nil {
			t.Fatalf("delete: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the delete never finished after the lock was released")
	}
}

// The run registration waits for the session's lock, so it cannot write the
// session back after a delete removed it.
func TestRunRegistrationWaitsForTheSessionsLock(t *testing.T) {
	svc, _ := newBlockingService(t)
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	unlock := svc.Store().LockSession(sess.ID)
	started := make(chan error, 1)
	go func() { started <- svc.StartGenerate(sess.ID, "学 Go", IntentArgs{}) }()
	select {
	case err := <-started:
		t.Fatalf("the run registration did not wait for the session's lock: %v", err)
	case <-time.After(300 * time.Millisecond):
	}
	unlock()
	select {
	case err := <-started:
		if err != nil {
			t.Fatalf("start: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the run registration never finished after the lock was released")
	}
}

// A deleted session does not come back. The run is still in flight when the
// delete lands, and the finalizer it wakes up afterwards must leave nothing
// behind: no session, no run state, no plan.
func TestDeletedSessionDoesNotComeBack(t *testing.T) {
	svc, llm := newBlockingService(t)
	store := svc.Store()
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.StartGenerate(sess.ID, "两周学会 React", IntentArgs{DailyMinutes: 60}); err != nil {
		t.Fatal(err)
	}
	llm.waitEntered(t)

	// The delete lands while the run is in flight: the run is cancelled and
	// the files are removed.
	if err := svc.DeleteSession(sess.ID); err != nil {
		t.Fatalf("delete: %v", err)
	}
	// Now let the run finish: its finalizer must not write anything.
	close(llm.release)

	// Watch for the session coming back. The window covers a finalizer that
	// was already inside its write phase when the delete landed.
	deadline := time.Now().Add(600 * time.Millisecond)
	for time.Now().Before(deadline) {
		if _, err := store.Session(sess.ID); err == nil {
			t.Fatal("the deleted session came back")
		} else if !errors.Is(err, ErrNotFound) {
			t.Fatalf("reading the deleted session: %v", err)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if _, err := store.Session(sess.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("the deleted session came back: %v", err)
	}
	if _, err := store.RunStateOf(sess.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("the deleted session's run state came back: %v", err)
	}
	plans, err := store.Plans()
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range plans {
		if p.SessionID == sess.ID {
			t.Fatalf("the deleted session's plan came back: %s", p.ID)
		}
	}
}
