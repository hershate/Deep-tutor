package tutor

import (
	"archive/zip"
	"bytes"
	"database/sql"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

func apkgQuiz(id, question, answer, explanation string, options ...string) Quiz {
	return Quiz{
		ID: id, PlanID: "p1", Type: QuizChoice,
		Payload: QuizPayload{Question: question, Options: options, Answer: answer, Explanation: explanation},
	}
}

func apkgAttempt(qid string, correct bool, at time.Time) QuizAttempt {
	c := correct
	return QuizAttempt{QuizID: qid, Correct: &c, At: at}
}

// readApkg opens the archive's collection.anki2 so assertions can inspect
// the schema-11 tables directly.
func readApkg(t *testing.T, blob []byte) *sql.DB {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(blob), int64(len(blob)))
	if err != nil {
		t.Fatalf("zip.NewReader: %v", err)
	}
	var collection []byte
	var media []byte
	for _, f := range zr.File {
		switch f.Name {
		case "collection.anki2":
			rc, rerr := f.Open()
			if rerr != nil {
				t.Fatalf("open collection: %v", rerr)
			}
			collection, _ = io.ReadAll(rc)
			rc.Close()
		case "media":
			rc, rerr := f.Open()
			if rerr != nil {
				t.Fatalf("open media: %v", rerr)
			}
			media, _ = io.ReadAll(rc)
			rc.Close()
		}
	}
	if len(collection) == 0 {
		t.Fatal("apkg holds no collection.anki2")
	}
	if string(media) != "{}" {
		t.Fatalf("media manifest = %q, want empty {}", media)
	}
	tmp, err := os.CreateTemp("", "apkg-test-*.anki2")
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
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func TestBuildApkgRoundTrip(t *testing.T) {
	plan := &Plan{ID: "plan-1", Title: "Go 并发", Topic: "Go"}
	quizzes := []Quiz{
		apkgQuiz("q1", "goroutine 是什么？", "轻量级线程", "由 Go 运行时调度", "线程", "轻量级线程", "进程"),
		apkgQuiz("q2", "<script>alert(1)</script> 危险吗？", "是<b>的</b>", ""),
	}
	now := time.Date(2026, 10, 14, 15, 4, 5, 0, time.UTC)
	blob, cards, err := BuildApkg(plan, quizzes, nil, false, now)
	if err != nil {
		t.Fatalf("BuildApkg: %v", err)
	}
	if cards != 2 {
		t.Fatalf("cards=%d want 2", cards)
	}
	db := readApkg(t, blob)

	var ver int
	if err := db.QueryRow(`SELECT ver FROM col WHERE id=1`).Scan(&ver); err != nil || ver != 11 {
		t.Fatalf("col.ver=%d err=%v, want schema 11", ver, err)
	}
	var decks string
	if err := db.QueryRow(`SELECT decks FROM col`).Scan(&decks); err != nil {
		t.Fatalf("read decks: %v", err)
	}
	if !strings.Contains(decks, "Reasonix Tutor::Go 并发") || strings.Contains(decks, "错题") {
		t.Fatalf("deck registry wrong: %s", decks)
	}
	var noteCount, cardCount int
	if err := db.QueryRow(`SELECT count(*) FROM notes`).Scan(&noteCount); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRow(`SELECT count(*) FROM cards`).Scan(&cardCount); err != nil {
		t.Fatal(err)
	}
	if noteCount != 2 || cardCount != 2 {
		t.Fatalf("notes=%d cards=%d, want 2/2", noteCount, cardCount)
	}

	rows, err := db.Query(`SELECT flds, sfld, csum, guid FROM notes ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	type note struct{ flds, sfld, guid string }
	var notes []note
	csums := map[string]int64{}
	for rows.Next() {
		var flds, sfld, guid string
		var csum int64
		if err := rows.Scan(&flds, &sfld, &csum, &guid); err != nil {
			t.Fatal(err)
		}
		notes = append(notes, note{flds, sfld, guid})
		csums[sfld] = csum
	}
	if !strings.Contains(notes[0].flds, "Q</b>：goroutine 是什么？") ||
		!strings.Contains(notes[0].flds, "<div class='opts'><div>线程</div><div>轻量级线程</div><div>进程</div></div>") ||
		!strings.Contains(notes[0].flds, "<div class='exp'>由 Go 运行时调度</div>") {
		t.Fatalf("card 0 fields wrong: %s", notes[0].flds)
	}
	if !strings.Contains(notes[0].flds, "\x1f") {
		t.Fatal("fields must be \\x1f-joined")
	}
	if strings.Contains(notes[1].flds, "<script>") {
		t.Fatalf("question must be HTML-escaped: %s", notes[1].flds)
	}
	if !strings.Contains(notes[1].flds, "&lt;script&gt;") || !strings.Contains(notes[1].flds, "是&lt;b&gt;的&lt;/b&gt;") {
		t.Fatalf("escaping wrong: %s", notes[1].flds)
	}
	for _, n := range notes {
		// The builder's own markup is stripped; escaped literals a user typed
		// (like &lt;script&gt;) legitimately unescape back to plain text.
		if strings.Contains(n.sfld, "<b>") || strings.Contains(n.sfld, "<div") {
			t.Fatalf("sort field must be plain text: %q", n.sfld)
		}
		if csum := apkgFieldChecksum(n.sfld); csum != csums[n.sfld] {
			t.Fatalf("csum mismatch for %q", n.sfld)
		}
	}
	if notes[0].guid == notes[1].guid || notes[0].guid == "" {
		t.Fatal("guids must be non-empty and distinct")
	}

	deckID := apkgDeckID("plan-1", false)
	var gotDeck int64
	if err := db.QueryRow(`SELECT did FROM cards ORDER BY id LIMIT 1`).Scan(&gotDeck); err != nil {
		t.Fatal(err)
	}
	if gotDeck != deckID {
		t.Fatalf("card deck=%d want %d", gotDeck, deckID)
	}
	var queue, due, ctype int
	if err := db.QueryRow(`SELECT type, queue, due FROM cards ORDER BY id LIMIT 1`).Scan(&ctype, &queue, &due); err != nil {
		t.Fatal(err)
	}
	if ctype != 0 || queue != 0 || due != 1 {
		t.Fatalf("new-card state wrong: type=%d queue=%d due=%d", ctype, queue, due)
	}
}

func TestBuildApkgDeterministic(t *testing.T) {
	plan := &Plan{ID: "stable", Topic: "T"}
	quizzes := []Quiz{apkgQuiz("q1", "1+1?", "2", "")}
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	b1, _, err := BuildApkg(plan, quizzes, nil, false, now)
	if err != nil {
		t.Fatal(err)
	}
	db1 := readApkg(t, b1)
	var guid1 string
	if err := db1.QueryRow(`SELECT guid FROM notes LIMIT 1`).Scan(&guid1); err != nil {
		t.Fatal(err)
	}
	var did1 int64
	if err := db1.QueryRow(`SELECT did FROM cards LIMIT 1`).Scan(&did1); err != nil {
		t.Fatal(err)
	}
	b2, _, err := BuildApkg(plan, quizzes, nil, false, now.Add(time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	db2 := readApkg(t, b2)
	var guid2 string
	if err := db2.QueryRow(`SELECT guid FROM notes LIMIT 1`).Scan(&guid2); err != nil {
		t.Fatal(err)
	}
	var did2 int64
	if err := db2.QueryRow(`SELECT did FROM cards LIMIT 1`).Scan(&did2); err != nil {
		t.Fatal(err)
	}
	if guid1 != guid2 {
		t.Fatalf("guid must be stable across exports: %q vs %q", guid1, guid2)
	}
	if did1 != did2 {
		t.Fatalf("deck id must be stable across exports: %d vs %d", did1, did2)
	}
}

func TestBuildApkgWrongOnly(t *testing.T) {
	plan := &Plan{ID: "p9", Topic: "错题"}
	t0 := time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC)
	quizzes := []Quiz{
		apkgQuiz("w", "wrong one?", "a1", ""),
		apkgQuiz("r", "right one?", "a2", ""),
		apkgQuiz("n", "never answered?", "a3", ""),
		apkgQuiz("f", "fixed later?", "a4", ""),
	}
	attempts := []QuizAttempt{
		apkgAttempt("w", false, t0),
		apkgAttempt("r", true, t0),
		apkgAttempt("f", false, t0),
		apkgAttempt("f", true, t0.Add(time.Minute)),
	}
	blob, cards, err := BuildApkg(plan, quizzes, attempts, true, t0)
	if err != nil {
		t.Fatalf("BuildApkg wrongOnly: %v", err)
	}
	if cards != 1 {
		t.Fatalf("cards=%d want 1 (latest-attempt-wrong only)", cards)
	}
	db := readApkg(t, blob)
	var flds string
	if err := db.QueryRow(`SELECT flds FROM notes`).Scan(&flds); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(flds, "wrong one?") {
		t.Fatalf("wrong-book holds the wrong quiz, got %s", flds)
	}
	var decks string
	if err := db.QueryRow(`SELECT decks FROM col`).Scan(&decks); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(decks, "·错题") {
		t.Fatalf("wrong deck name missing: %s", decks)
	}
	var did int64
	if err := db.QueryRow(`SELECT did FROM cards`).Scan(&did); err != nil {
		t.Fatal(err)
	}
	if did != apkgDeckID("p9", true) || did == apkgDeckID("p9", false) {
		t.Fatalf("wrong deck id %d must sit a gap away from the full deck", did)
	}
}

func TestBuildApkgWrongOnlyEmptyRefusal(t *testing.T) {
	plan := &Plan{ID: "p10", Topic: "全对"}
	quizzes := []Quiz{apkgQuiz("r", "right?", "yes", "")}
	if _, _, err := BuildApkg(plan, quizzes, []QuizAttempt{apkgAttempt("r", true, time.Now())}, true, time.Now()); !errors.Is(err, ErrNoWrongQuizzes) {
		t.Fatalf("err=%v want ErrNoWrongQuizzes", err)
	}
	if _, _, err := BuildApkg(plan, quizzes, nil, true, time.Now()); !errors.Is(err, ErrNoWrongQuizzes) {
		t.Fatalf("no attempts: err=%v want ErrNoWrongQuizzes", err)
	}
}

func TestBuildApkgSkipsIncompleteAndDupes(t *testing.T) {
	plan := &Plan{ID: "p11", Topic: "过滤"}
	quizzes := []Quiz{
		apkgQuiz("ok", "valid?", "yes", ""),
		apkgQuiz("noanswer", "no answer?", "  ", ""),
		apkgQuiz("noquestion", "  ", "answer", ""),
		apkgQuiz("dup", "valid?", "yes", ""),
	}
	blob, cards, err := BuildApkg(plan, quizzes, nil, false, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if cards != 1 {
		t.Fatalf("cards=%d want 1 (skip empty sides, dedupe repeats)", cards)
	}
	db := readApkg(t, blob)
	var n int
	if err := db.QueryRow(`SELECT count(*) FROM notes`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("notes=%d want 1", n)
	}
}

func TestBuildApkgEmptyRefusal(t *testing.T) {
	plan := &Plan{ID: "p12", Topic: "空"}
	if _, _, err := BuildApkg(plan, nil, nil, false, time.Now()); !errors.Is(err, ErrNoQuizzes) {
		t.Fatalf("err=%v want ErrNoQuizzes", err)
	}
	if _, _, err := BuildApkg(plan, []Quiz{apkgQuiz("e", "no answer", "", "")}, nil, false, time.Now()); !errors.Is(err, ErrNoQuizzes) {
		t.Fatalf("err=%v want ErrNoQuizzes after filtering", err)
	}
}
