package tutor

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// The library's dedup check, its document count and the row write share one
// lock. Before that they were three separate steps, so concurrent uploads
// of the same content both missed the dedup and left two rows with one
// hash.
func TestKbUploadUnderRaceKeepsOneRowPerHash(t *testing.T) {
	svc := newShareService(t)
	data := bytes.Repeat([]byte("same-content-"), 64)

	const writers = 8
	ids := make([]string, writers)
	errs := make([]error, writers)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range writers {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			doc, err := svc.KbUpload("doc.txt", data)
			if err != nil {
				errs[i] = err
				return
			}
			ids[i] = doc.ID
		}(i)
	}
	close(start)
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("upload %d: %v", i, err)
		}
	}
	for i := 1; i < writers; i++ {
		if ids[i] != ids[0] {
			t.Fatalf("upload %d returned id %s, want the shared %s", i, ids[i], ids[0])
		}
	}
	docs, err := svc.store.KbDocs()
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 1 {
		t.Fatalf("rows after %d concurrent uploads of one content: %d, want 1", writers, len(docs))
	}
}

// The document cap holds when writers race: the count and the write share
// one lock, so two writers cannot both pass the check.
func TestKbUploadUnderRaceHoldsTheDocumentCap(t *testing.T) {
	svc := newShareService(t)
	// Fill the library to one below the cap.
	for i := range KbMaxDocs - 1 {
		if _, err := svc.KbUpload(fmt.Sprintf("fill-%d.txt", i), []byte(strings.Repeat("f", i+1))); err != nil {
			t.Fatalf("fill %d: %v", i, err)
		}
	}

	const writers = 8
	errs := make([]error, writers)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range writers {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			// Each upload carries its own content, so the dedup never
			// applies: only the count decides.
			_, errs[i] = svc.KbUpload(fmt.Sprintf("race-%d.txt", i), []byte(strings.Repeat("x", i+1)))
		}(i)
	}
	close(start)
	wg.Wait()

	ok := 0
	for i, err := range errs {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrKbQuota):
		default:
			t.Fatalf("upload %d: unexpected error %v", i, err)
		}
	}
	if ok != 1 {
		t.Fatalf("%d writers passed the cap, want 1", ok)
	}
	docs, err := svc.store.KbDocs()
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != KbMaxDocs {
		t.Fatalf("rows after the race: %d, want %d", len(docs), KbMaxDocs)
	}
}

// The per-session material cap was declared as a constant and enforced
// nowhere: the session's materials directory grew without bound.
func TestAddMaterialUnderRaceHoldsThePerSessionCap(t *testing.T) {
	svc := newShareService(t)
	sess, err := svc.store.CreateSession("s")
	if err != nil {
		t.Fatal(err)
	}

	const writers = maxMaterialsPerSession + 8
	errs := make([]error, writers)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range writers {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			_, errs[i] = svc.AddMaterialText(sess.ID, fmt.Sprintf("m%d", i), fmt.Sprintf("body %d", i))
		}(i)
	}
	close(start)
	wg.Wait()

	ok := 0
	for i, err := range errs {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrMaterialQuota):
		default:
			t.Fatalf("upload %d: unexpected error %v", i, err)
		}
	}
	if ok != maxMaterialsPerSession {
		t.Fatalf("%d materials accepted, want %d", ok, maxMaterialsPerSession)
	}
	materials, err := svc.store.Materials(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(materials) != maxMaterialsPerSession {
		t.Fatalf("materials after the race: %d, want %d", len(materials), maxMaterialsPerSession)
	}
}

// A refused material leaves no original file behind.
func TestAddMaterialQuotaRefusalLeavesNoOriginal(t *testing.T) {
	svc := newShareService(t)
	sess, err := svc.store.CreateSession("s")
	if err != nil {
		t.Fatal(err)
	}
	for i := range maxMaterialsPerSession {
		if _, err := svc.AddMaterialText(sess.ID, fmt.Sprintf("m%d", i), "body"); err != nil {
			t.Fatalf("fill %d: %v", i, err)
		}
	}
	if _, err := svc.AddMaterialText(sess.ID, "over", "body"); !errors.Is(err, ErrMaterialQuota) {
		t.Fatalf("over the cap: %v", err)
	}
	entries, err := svc.store.Materials(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	originals, err := os.ReadDir(filepath.Join(svc.store.sessionDir(sess.ID), "originals"))
	if err != nil {
		t.Fatal(err)
	}
	if len(originals) != len(entries) {
		t.Fatalf("%d originals for %d material rows: a refused upload left an orphan", len(originals), len(entries))
	}
}

// A progress row is written for a node of the plan. An unknown id used to
// create a row no view ever reads, and the file grew with every request.
func TestRecordProgressRejectsAnUnknownNode(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{
		ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now(),
		Knowledge: KnowledgeMap{Nodes: []KnowledgeNode{{SID: "k1", Name: "节点一"}}},
	}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.RecordProgress(plan.ID, "not-a-node", ProgressDone, nil); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown node: %v", err)
	}
	rows, err := svc.store.ProgressList(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatalf("a refused update wrote %d rows", len(rows))
	}
	// The plan's own node still records.
	if _, err := svc.RecordProgress(plan.ID, "k1", ProgressDone, nil); err != nil {
		t.Fatalf("known node: %v", err)
	}
	rows, err = svc.store.ProgressList(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows after the known node: %d, want 1", len(rows))
	}
}

// The node id is bounded, like the Python backend's node_id field.
func TestRecordProgressRejectsAnOverlongNodeID(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	long := strings.Repeat("n", maxProgressNodeID+1)
	if _, err := svc.RecordProgress(plan.ID, long, ProgressDone, nil); !errors.Is(err, ErrBadProgressInput) {
		t.Fatalf("overlong node id: %v", err)
	}
	rows, err := svc.store.ProgressList(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatalf("a refused update wrote %d rows", len(rows))
	}
}
