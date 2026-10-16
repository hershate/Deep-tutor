package tutor

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// store_cache_test.go pins the cache's freshness contract: edits, deletions,
// and additions made behind the store's back are visible on the next call —
// the same contract as the uncached decode the cache replaces.

func rewriteJSON(t *testing.T, path string, v any) {
	t.Helper()
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir for %s: %v", path, err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatalf("rewrite %s: %v", path, err)
	}
}

func TestListPathsSeeExternalEdits(t *testing.T) {
	store := openTestStore(t)
	sess, err := store.CreateSession("原标题")
	if err != nil {
		t.Fatalf("CreateSession: %v", err)
	}
	note := &Note{ID: "note001", Title: "原笔记", Body: "原始内容"}
	if err := store.SaveNote(note); err != nil {
		t.Fatalf("SaveNote: %v", err)
	}
	plan := &Plan{
		ID: "plan001", Version: 1, Title: "方案", Topic: "主题",
		Knowledge: KnowledgeMap{Nodes: []KnowledgeNode{{SID: "n1", Name: "基础概念"}}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatalf("SavePlan: %v", err)
	}
	// Warm every cache before touching any file behind the store's back.
	if list, err := store.Sessions(); err != nil || len(list) != 1 {
		t.Fatalf("warm Sessions: %v %d", err, len(list))
	}
	if notes, err := store.Notes(); err != nil || len(notes) != 1 {
		t.Fatalf("warm Notes: %v %d", err, len(notes))
	}
	if res, err := SearchAll(store, "外部编辑词", 10); err != nil || res.Nodes.Total != 0 {
		t.Fatalf("warm SearchAll: %v %+v", err, res.Nodes.Total)
	}

	sess.Title = "外部改写后的标题"
	rewriteJSON(t, filepath.Join(store.sessionDir(sess.ID), "session.json"), sess)
	list, err := store.Sessions()
	if err != nil || len(list) != 1 || list[0].Title != "外部改写后的标题" {
		t.Fatalf("Sessions after external edit: %v %+v", err, list)
	}
	note.Body = "外部改写后的正文"
	rewriteJSON(t, filepath.Join(store.notesDir(), "note001.json"), note)
	if notes, err := store.Notes(); err != nil || len(notes) != 1 || notes[0].Body != "外部改写后的正文" {
		t.Fatalf("Notes after external edit: %v %+v", err, notes)
	}
	plan.Knowledge.Nodes = append(plan.Knowledge.Nodes, KnowledgeNode{SID: "n2", Name: "外部编辑词考点"})
	rewriteJSON(t, filepath.Join(store.planDir("plan001"), "plan.json"), plan)
	if res, err := SearchAll(store, "外部编辑词", 10); err != nil || res.Nodes.Total != 1 {
		t.Fatalf("SearchAll after external edit: %v %+v", err, res.Nodes.Total)
	}
	// Plans() reads index.json behind a stamp gate; an external rewrite of
	// that file must come through on the next call.
	var idx planIndex
	if err := store.readJSON(filepath.Join(store.Root(), "plans", "index.json"), &idx); err != nil {
		t.Fatalf("read index: %v", err)
	}
	if len(idx.Plans) != 1 {
		t.Fatalf("index plans: %+v", idx.Plans)
	}
	idx.Plans[0].Title = "外部改写的方案标题"
	rewriteJSON(t, filepath.Join(store.Root(), "plans", "index.json"), idx)
	plans, err := store.Plans()
	if err != nil || len(plans) != 1 || plans[0].Title != "外部改写的方案标题" {
		t.Fatalf("Plans after external index edit: %v %+v", err, plans)
	}

	if err := os.RemoveAll(store.sessionDir(sess.ID)); err != nil {
		t.Fatalf("remove session dir: %v", err)
	}
	if err := os.Remove(filepath.Join(store.notesDir(), "note001.json")); err != nil {
		t.Fatalf("remove note: %v", err)
	}
	if list, err := store.Sessions(); err != nil || len(list) != 0 {
		t.Fatalf("Sessions after external delete: %v %d", err, len(list))
	}
	if notes, err := store.Notes(); err != nil || len(notes) != 0 {
		t.Fatalf("Notes after external delete: %v %d", err, len(notes))
	}

	fresh := &GenSession{ID: "extsess", Title: "外部新建", CreatedAt: time.Now(), UpdatedAt: time.Now(), Status: StatusIdle}
	rewriteJSON(t, filepath.Join(store.sessionDir("extsess"), "session.json"), fresh)
	freshNote := Note{ID: "extnote", Title: "外部新建笔记", Body: "内容", CreatedAt: time.Now(), UpdatedAt: time.Now()}
	rewriteJSON(t, filepath.Join(store.notesDir(), "extnote.json"), freshNote)
	if list, err := store.Sessions(); err != nil || len(list) != 1 || list[0].ID != "extsess" {
		t.Fatalf("Sessions after external create: %v %+v", err, list)
	}
	if notes, err := store.Notes(); err != nil || len(notes) != 1 || notes[0].ID != "extnote" {
		t.Fatalf("Notes after external create: %v %+v", err, notes)
	}
}

// TestReturnedSessionsArePrivateToTheCaller pins the copy contract: mutating
// a session handed out by Sessions must not leak into the next listing.
func TestReturnedSessionsArePrivateToTheCaller(t *testing.T) {
	store := openTestStore(t)
	sess, err := store.CreateSession("共享前标题")
	if err != nil {
		t.Fatalf("CreateSession: %v", err)
	}
	sess.ClarifyQuestions = []string{"问题一"}
	if err := store.SaveSession(sess); err != nil {
		t.Fatalf("SaveSession: %v", err)
	}
	list, err := store.Sessions()
	if err != nil || len(list) != 1 {
		t.Fatalf("Sessions: %v %d", err, len(list))
	}
	list[0].Title = "调用方改写"
	list[0].ClarifyQuestions[0] = "调用方篡改"
	again, err := store.Sessions()
	if err != nil || len(again) != 1 {
		t.Fatalf("Sessions again: %v %d", err, len(again))
	}
	if again[0].Title != "共享前标题" || again[0].ClarifyQuestions[0] != "问题一" {
		t.Fatalf("cached session mutated by caller: %+v", again[0])
	}
}

func TestReindexReusesStampedSummaries(t *testing.T) {
	store := openTestStore(t)
	mustPlan := func(id string, version int) *Plan {
		return &Plan{
			ID: id, Version: version, Title: "方案 " + id, Topic: "主题",
			CreatedAt: time.Now(), UpdatedAt: time.Now(),
		}
	}
	if err := store.SavePlan(mustPlan("planA", 1)); err != nil {
		t.Fatalf("SavePlan A: %v", err)
	}
	if err := store.SavePlan(mustPlan("planB", 1)); err != nil {
		t.Fatalf("SavePlan B: %v", err)
	}
	readIndex := func() planIndex {
		t.Helper()
		var idx planIndex
		if err := store.readJSON(filepath.Join(store.Root(), "plans", "index.json"), &idx); err != nil {
			t.Fatalf("read index: %v", err)
		}
		return idx
	}
	idx := readIndex()
	if len(idx.Plans) != 2 || len(idx.Stamps) != 2 {
		t.Fatalf("index carries plans and stamps: %+v %+v", idx.Plans, idx.Stamps)
	}

	// A legacy index without stamps still reindexes correctly.
	legacy := planIndex{Plans: idx.Plans}
	rewriteJSON(t, filepath.Join(store.Root(), "plans", "index.json"), legacy)
	if err := store.SavePlan(mustPlan("planA", 2)); err != nil {
		t.Fatalf("SavePlan A v2: %v", err)
	}
	idx = readIndex()
	if len(idx.Plans) != 2 || len(idx.Stamps) != 2 {
		t.Fatalf("legacy index upgraded: %+v %+v", idx.Plans, idx.Stamps)
	}
	stampA := idx.Stamps["planA"]

	// An unchanged plan keeps its summary via its stamp; a changed one
	// re-decodes; an externally deleted dir drops out.
	if err := store.SavePlan(mustPlan("planB", 2)); err != nil {
		t.Fatalf("SavePlan B v2: %v", err)
	}
	idx = readIndex()
	if idx.Stamps["planA"] != stampA {
		t.Fatalf("unchanged plan re-decoded: %+v -> %+v", stampA, idx.Stamps["planA"])
	}
	var versionB int
	for _, p := range idx.Plans {
		if p.ID == "planB" {
			versionB = p.Version
		}
	}
	if versionB != 2 {
		t.Fatalf("changed plan summary stale: %+v", idx.Plans)
	}
	if err := os.RemoveAll(store.planDir("planA")); err != nil {
		t.Fatalf("remove planA: %v", err)
	}
	if err := store.SavePlan(mustPlan("planB", 3)); err != nil {
		t.Fatalf("SavePlan B v3: %v", err)
	}
	idx = readIndex()
	if len(idx.Plans) != 1 || idx.Plans[0].ID != "planB" {
		t.Fatalf("deleted plan pruned: %+v", idx.Plans)
	}
	plans, err := store.Plans()
	if err != nil || len(plans) != 1 || plans[0].Version != 3 {
		t.Fatalf("Plans after incremental reindex: %v %+v", err, plans)
	}
}

// TestCacheSurvivesConcurrentReadWrite hammers the cached list paths while a
// writer keeps saving; -race turns this into the mutex's proof.
func TestCacheSurvivesConcurrentReadWrite(t *testing.T) {
	store := openTestStore(t)
	if err := store.SaveNote(&Note{ID: "n0", Title: "t", Body: "b"}); err != nil {
		t.Fatalf("SaveNote: %v", err)
	}
	plan := &Plan{ID: "planP", Version: 1, Title: "方案", Topic: "主题",
		Knowledge: KnowledgeMap{Nodes: []KnowledgeNode{{SID: "x", Name: "钩子概念"}}}}
	if err := store.SavePlan(plan); err != nil {
		t.Fatalf("SavePlan: %v", err)
	}
	var wg sync.WaitGroup
	for range 4 {
		wg.Go(func() {
			for i := range 50 {
				if _, err := store.Sessions(); err != nil {
					t.Errorf("Sessions: %v", err)
					return
				}
				if _, err := store.Notes(); err != nil {
					t.Errorf("Notes: %v", err)
					return
				}
				if _, err := SearchAll(store, "钩子", 10); err != nil {
					t.Errorf("SearchAll: %v", err)
					return
				}
				if i%10 == 0 {
					sess := &GenSession{ID: "raceSess", Title: "并发", Status: StatusIdle,
						CreatedAt: time.Now(), UpdatedAt: time.Now()}
					if err := store.SaveSession(sess); err != nil {
						t.Errorf("SaveSession: %v", err)
						return
					}
				}
			}
		})
	}
	wg.Wait()
}
