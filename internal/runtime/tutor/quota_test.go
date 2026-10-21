package tutor

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

// repeat builds a string of n copies of s.
func repeat(s string, n int) string {
	return strings.Repeat(s, n)
}

// The notes cap has to hold when writers race: the count and the write share
// one lock, so two writers cannot both pass the check.
func TestSaveNoteGuardedHoldsTheQuotaUnderRace(t *testing.T) {
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	// Fill the library to one below the cap.
	for i := 0; i < noteQuota-1; i++ {
		if err := store.SaveNoteGuarded(&Note{Title: fmt.Sprintf("n%d", i), Body: "b"}); err != nil {
			t.Fatalf("fill %d: %v", i, err)
		}
	}

	const writers = 8
	var wg sync.WaitGroup
	results := make([]error, writers)
	start := make(chan struct{})
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			results[i] = store.SaveNoteGuarded(&Note{Title: "race", Body: "b"})
		}(i)
	}
	close(start)
	wg.Wait()

	ok := 0
	for _, err := range results {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrNoteQuota):
		default:
			t.Fatalf("unexpected error: %v", err)
		}
	}
	if ok != 1 {
		t.Fatalf("%d writers passed the quota, want 1", ok)
	}
	notes, err := store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != noteQuota {
		t.Fatalf("notes after the race: %d, want %d", len(notes), noteQuota)
	}
}

// A note is bounded on every write path, not only on the import path.
func TestSaveNoteGuardedClampsTitleAndBody(t *testing.T) {
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	note := &Note{Title: repeat("t", noteTitleMax+50), Body: repeat("b", noteBodyMax+50)}
	if err := store.SaveNoteGuarded(note); err != nil {
		t.Fatal(err)
	}
	if len([]rune(note.Title)) != noteTitleMax {
		t.Fatalf("title length: %d", len([]rune(note.Title)))
	}
	if len([]rune(note.Body)) != noteBodyMax {
		t.Fatalf("body length: %d", len([]rune(note.Body)))
	}
}

// The sessions cap has to hold when writers race.
func TestCreateSessionHoldsTheQuotaUnderRace(t *testing.T) {
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < maxSessions-1; i++ {
		if _, err := store.CreateSession("s"); err != nil {
			t.Fatalf("fill %d: %v", i, err)
		}
	}
	const writers = 8
	var wg sync.WaitGroup
	results := make([]error, writers)
	start := make(chan struct{})
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			_, results[i] = store.CreateSession("race")
		}(i)
	}
	close(start)
	wg.Wait()

	ok := 0
	for _, err := range results {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrSessionQuota):
		default:
			t.Fatalf("unexpected error: %v", err)
		}
	}
	if ok != 1 {
		t.Fatalf("%d writers passed the quota, want 1", ok)
	}
}

// Concurrent answers must not lose one another: the attempt list is
// read-modify-written, so the read and the write share the plan's lock.
func TestAppendAttemptKeepsEveryConcurrentAnswer(t *testing.T) {
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}

	const writers = 16
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			if err := store.AppendAttempt(plan.ID, QuizAttempt{QuizID: fmt.Sprintf("q%d", i), At: time.Now()}); err != nil {
				t.Errorf("append %d: %v", i, err)
			}
		}(i)
	}
	close(start)
	wg.Wait()

	attempts, err := store.Attempts(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != writers {
		t.Fatalf("attempts after %d concurrent writers: %d", writers, len(attempts))
	}
}

// The same for the learning ledger.
func TestAddLogKeepsEveryConcurrentCheckIn(t *testing.T) {
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}

	const writers = 16
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			if err := store.AddLog(&LearningLog{PlanID: plan.ID, Date: "2026-10-21", Minutes: 10}); err != nil {
				t.Errorf("log %d: %v", i, err)
			}
		}(i)
	}
	close(start)
	wg.Wait()

	logs, err := store.Logs(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(logs) != writers {
		t.Fatalf("logs after %d concurrent writers: %d", writers, len(logs))
	}
}

// A check-in is bounded: the ledger keeps every entry in one file.
func TestAddLogRejectsNonPositiveMinutes(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if err := svc.AddLog(&LearningLog{PlanID: plan.ID, Minutes: 0}); !errors.Is(err, ErrBadLogInput) {
		t.Fatalf("minutes 0: %v", err)
	}
	if err := svc.AddLog(&LearningLog{PlanID: plan.ID, Minutes: 10, Note: "note"}); err != nil {
		t.Fatal(err)
	}
}
