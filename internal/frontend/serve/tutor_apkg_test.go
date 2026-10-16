package serve

import (
	"archive/zip"
	"bytes"
	"database/sql"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"reasonix/internal/runtime/tutor"
)

// TestTutorApkgExport walks the export route end to end: a seeded plan with
// quizzes and attempts exports both the full deck and the wrong-book deck,
// and each refusal path answers its own code.
func TestTutorApkgExport(t *testing.T) {
	srv, store := newShareTestServer(t)

	sess, err := store.CreateSession("apkg-flow")
	if err != nil {
		t.Fatal(err)
	}
	plan := &tutor.Plan{
		ID: "plan1", SessionID: sess.ID, Version: 1,
		Title: "Go 并发", Topic: "Go",
		CreatedAt: time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC),
		Stages:    []tutor.Stage{{Index: 1, Name: "阶段一", Goal: "并发基础", Milestone: "能写 goroutine"}},
		Schedule:  []tutor.ScheduleDay{{Date: "2026-10-15", StageIndex: 1, Tasks: []string{"读并发一章"}, EstMinutes: 30}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveQuizzes(plan.ID, []tutor.Quiz{
		{ID: "q1", PlanID: plan.ID, Type: tutor.QuizChoice,
			Payload: tutor.QuizPayload{Question: "channel 用于什么？", Options: []string{"通信", "存储"}, Answer: "通信", Explanation: "不要通过共享内存来通信"}},
		{ID: "q2", PlanID: plan.ID, Type: tutor.QuizFill,
			Payload: tutor.QuizPayload{Question: "sync.WaitGroup 等待什么？", Answer: "一组 goroutine 结束"}},
	}); err != nil {
		t.Fatal(err)
	}
	sess.PlanID = plan.ID
	if err := store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}

	fetch := func(path string) (*http.Response, string) {
		t.Helper()
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp, string(body)
	}

	// Full deck: two cards, Anki archive headers, valid collection inside.
	resp, body := fetch("/tutor/plans/plan1/export.apkg")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("full export status=%d body=%s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Content-Type"); got != "application/octet-stream" {
		t.Fatalf("content-type=%q", got)
	}
	if cd := resp.Header.Get("Content-Disposition"); !strings.Contains(cd, `filename="plan-plan1.apkg"`) {
		t.Fatalf("disposition=%q", cd)
	}
	if resp.Header.Get("X-Tutor-Cards") != "2" {
		t.Fatalf("cards header=%q want 2", resp.Header.Get("X-Tutor-Cards"))
	}
	assertApkgDeck(t, []byte(body), "Reasonix Tutor::Go 并发", 2)

	// Wrong-only: q1 answered wrong, q2 never answered.
	wrong := false
	if err := store.AppendAttempt(plan.ID, tutor.QuizAttempt{QuizID: "q1", Selected: "存储", Correct: &wrong, At: time.Now()}); err != nil {
		t.Fatal(err)
	}
	resp, body = fetch("/tutor/plans/plan1/export.apkg?wrong_only=1")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("wrong export status=%d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(resp.Header.Get("Content-Disposition"), "plan-plan1-wrong.apkg") {
		t.Fatalf("wrong disposition=%q", resp.Header.Get("Content-Disposition"))
	}
	if resp.Header.Get("X-Tutor-Cards") != "1" {
		t.Fatalf("wrong cards header=%q want 1", resp.Header.Get("X-Tutor-Cards"))
	}
	assertApkgDeck(t, []byte(body), "Reasonix Tutor::Go 并发·错题", 1)

	// Refusals: missing plan, no quizzes, empty wrong-book.
	resp, body = fetch("/tutor/plans/missing/export.apkg")
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(body, "tutor.not_found") {
		t.Fatalf("missing plan: status=%d body=%s", resp.StatusCode, body)
	}
	plan2 := &tutor.Plan{ID: "plan2", SessionID: sess.ID, Version: 1, Topic: "空", CreatedAt: time.Now()}
	if err := store.SavePlan(plan2); err != nil {
		t.Fatal(err)
	}
	resp, body = fetch("/tutor/plans/plan2/export.apkg")
	if resp.StatusCode != http.StatusBadRequest || !strings.Contains(body, "tutor.no_quizzes") {
		t.Fatalf("no quizzes: status=%d body=%s", resp.StatusCode, body)
	}
	resp, body = fetch("/tutor/plans/plan2/export.apkg?wrong_only=1")
	if resp.StatusCode != http.StatusBadRequest || !strings.Contains(body, "tutor.no_wrong_quizzes") {
		t.Fatalf("no wrong: status=%d body=%s", resp.StatusCode, body)
	}
}

// assertApkgDeck opens the archive and checks the deck name plus note count.
func assertApkgDeck(t *testing.T, blob []byte, deckName string, notes int) {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(blob), int64(len(blob)))
	if err != nil {
		t.Fatalf("zip: %v", err)
	}
	var collection []byte
	var media []byte
	for _, f := range zr.File {
		switch f.Name {
		case "collection.anki2":
			rc, _ := f.Open()
			collection, _ = io.ReadAll(rc)
			rc.Close()
		case "media":
			rc, _ := f.Open()
			media, _ = io.ReadAll(rc)
			rc.Close()
		}
	}
	if len(collection) == 0 || string(media) != "{}" {
		t.Fatalf("archive entries wrong (collection=%d media=%q)", len(collection), media)
	}
	tmp, err := os.CreateTemp("", "apkg-serve-*.anki2")
	if err != nil {
		t.Fatal(err)
	}
	path := tmp.Name()
	tmp.Close()
	t.Cleanup(func() { os.Remove(path) })
	if err := os.WriteFile(path, collection, 0o600); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var decks string
	if err := db.QueryRow(`SELECT decks FROM col`).Scan(&decks); err != nil {
		t.Fatalf("col: %v", err)
	}
	if !strings.Contains(decks, deckName) {
		t.Fatalf("deck %q missing from %s", deckName, decks)
	}
	var n int
	if err := db.QueryRow(`SELECT count(*) FROM notes`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != notes {
		t.Fatalf("notes=%d want %d", n, notes)
	}
}
