package bench

import (
	"path/filepath"
	"testing"

	"reasonix/internal/runtime/tutor"
)

// store_bench_test.go measures the read and write paths a Studio session
// hits: global search over the whole library, the session list, and saving
// a plan version (which reindexes every plan).

const (
	seedPlansCount  = 60
	seedNodesPer    = 120
	seedSessionsNum = 200
	seedNotesNum    = 100
)

func BenchmarkSearchAll(b *testing.B) {
	store := newStore(b)
	seedPlans(b, store, seedPlansCount, seedNodesPer)
	seedNotes(b, store, seedNotesNum)
	b.ResetTimer()
	for b.Loop() {
		results, err := tutor.SearchAll(store, "React", 10)
		if err != nil {
			b.Fatalf("SearchAll: %v", err)
		}
		if results.Nodes.Total == 0 || results.Notes.Total == 0 {
			b.Fatalf("fixture lost its needles: %+v", results.Nodes.Total)
		}
	}
}

func BenchmarkSearchAllNoHit(b *testing.B) {
	store := newStore(b)
	seedPlans(b, store, seedPlansCount, seedNodesPer)
	seedNotes(b, store, seedNotesNum)
	b.ResetTimer()
	for b.Loop() {
		if _, err := tutor.SearchAll(store, "不存在的检索词xyz", 10); err != nil {
			b.Fatalf("SearchAll: %v", err)
		}
	}
}

// BenchmarkSearchAllColdStore reopens the store every iteration, so each
// search runs with an empty decode cache — the cost a first hit or a fresh
// process pays, and the ceiling the warm benchmark must never hide.
func BenchmarkSearchAllColdStore(b *testing.B) {
	root := filepath.Join(b.TempDir(), "tutor")
	store, err := tutor.OpenStore(root)
	if err != nil {
		b.Fatalf("OpenStore: %v", err)
	}
	seedPlans(b, store, seedPlansCount, seedNodesPer)
	seedNotes(b, store, seedNotesNum)
	b.ResetTimer()
	for b.Loop() {
		cold, err := tutor.OpenStore(root)
		if err != nil {
			b.Fatalf("OpenStore: %v", err)
		}
		results, err := tutor.SearchAll(cold, "React", 10)
		if err != nil {
			b.Fatalf("SearchAll: %v", err)
		}
		if results.Nodes.Total == 0 || results.Notes.Total == 0 {
			b.Fatalf("fixture lost its needles: %+v", results.Nodes.Total)
		}
	}
}

func BenchmarkSessionsList(b *testing.B) {
	store := newStore(b)
	seedSessions(b, store, seedSessionsNum)
	b.ResetTimer()
	for b.Loop() {
		sessions, err := store.Sessions()
		if err != nil {
			b.Fatalf("Sessions: %v", err)
		}
		if len(sessions) != seedSessionsNum {
			b.Fatalf("sessions = %d", len(sessions))
		}
	}
}

// BenchmarkSavePlanReindex isolates the write path: every save rewalks and
// decodes every plan to rebuild the index.
func BenchmarkSavePlanReindex(b *testing.B) {
	store := newStore(b)
	seedPlans(b, store, seedPlansCount, seedNodesPer)
	b.ResetTimer()
	for b.Loop() {
		p, err := store.Plan("plan000")
		if err != nil {
			b.Fatalf("Plan: %v", err)
		}
		p.Version++
		if err := store.SavePlan(p); err != nil {
			b.Fatalf("SavePlan: %v", err)
		}
	}
}
