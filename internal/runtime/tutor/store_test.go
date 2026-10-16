package tutor

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func openTestStore(t *testing.T) *Store {
	t.Helper()
	root := filepath.Join(t.TempDir(), "tutor")
	store, err := OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	// Windows reports "directory not empty" when a remove races a just-ended
	// rename, and t.TempDir does not retry — clear the store ourselves first.
	t.Cleanup(func() {
		for range 20 {
			if err := os.RemoveAll(root); err == nil {
				return
			}
			time.Sleep(25 * time.Millisecond)
		}
	})
	return store
}

func TestSessionRoundTripAndList(t *testing.T) {
	store := openTestStore(t)
	sess, err := store.CreateSession("学 Go")
	if err != nil {
		t.Fatalf("CreateSession: %v", err)
	}
	if sess.ID == "" || sess.Status != StatusIdle {
		t.Fatalf("new session minted idle: %+v", sess)
	}
	sess.Status = StatusRunning
	sess.CurrentTool = "understand_intent"
	if err := store.SaveSession(sess); err != nil {
		t.Fatalf("SaveSession: %v", err)
	}
	got, err := store.Session(sess.ID)
	if err != nil {
		t.Fatalf("Session: %v", err)
	}
	if got.Status != StatusRunning || got.CurrentTool != "understand_intent" {
		t.Fatalf("session persists: %+v", got)
	}
	list, err := store.Sessions()
	if err != nil || len(list) != 1 {
		t.Fatalf("Sessions lists one: %v %d", err, len(list))
	}
}

func TestSessionMissingIsErrNotFound(t *testing.T) {
	store := openTestStore(t)
	if _, err := store.Session("nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing session is ErrNotFound: %v", err)
	}
}

func TestDeleteSessionCascadesPlans(t *testing.T) {
	store := openTestStore(t)
	sess, _ := store.CreateSession("cascade")
	plan := &Plan{ID: "plan1", SessionID: sess.ID, Version: 1, Title: "t", CreatedAt: time.Now()}
	if err := store.SavePlan(plan); err != nil {
		t.Fatalf("SavePlan: %v", err)
	}
	sess.PlanID = plan.ID
	if err := store.SaveSession(sess); err != nil {
		t.Fatalf("SaveSession: %v", err)
	}
	if err := store.DeleteSession(sess.ID); err != nil {
		t.Fatalf("DeleteSession: %v", err)
	}
	if _, err := store.Session(sess.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("session gone: %v", err)
	}
	if _, err := store.Plan(plan.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("plan cascade: %v", err)
	}
	plans, err := store.Plans()
	if err != nil || len(plans) != 0 {
		t.Fatalf("index drops cascaded plans: %v %d", err, len(plans))
	}
}

func TestMaterialLifecycle(t *testing.T) {
	store := openTestStore(t)
	sess, _ := store.CreateSession("materials")
	m := &Material{ID: "m1", SessionID: sess.ID, Kind: MaterialFile, Source: "notes.pdf", Status: "pending", CreatedAt: time.Now()}
	if err := store.SaveMaterial(m); err != nil {
		t.Fatalf("SaveMaterial: %v", err)
	}
	if _, err := store.WriteMaterialOriginal(sess.ID, m.ID, "pdf", []byte("%PDF-fake")); err != nil {
		t.Fatalf("WriteMaterialOriginal: %v", err)
	}
	if err := store.WriteMaterialParsed(sess.ID, m.ID, "parsed text"); err != nil {
		t.Fatalf("WriteMaterialParsed: %v", err)
	}
	text, err := store.ReadMaterialParsed(sess.ID, m.ID)
	if err != nil || text != "parsed text" {
		t.Fatalf("ReadMaterialParsed: %v %q", err, text)
	}
	list, err := store.Materials(sess.ID)
	if err != nil || len(list) != 1 {
		t.Fatalf("Materials: %v %d", err, len(list))
	}
	if err := store.DeleteMaterial(sess.ID, m.ID); err != nil {
		t.Fatalf("DeleteMaterial: %v", err)
	}
	if _, err := store.ReadMaterialParsed(sess.ID, m.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("parsed text removed with the material: %v", err)
	}
}

func TestWriteMaterialOriginalSanitizesExt(t *testing.T) {
	store := openTestStore(t)
	sess, _ := store.CreateSession("sanitize")
	path, err := store.WriteMaterialOriginal(sess.ID, "m1", "../../etc/passwd", []byte("x"))
	if err != nil {
		t.Fatalf("WriteMaterialOriginal: %v", err)
	}
	if filepath.Base(path) != "m1.bin" {
		t.Fatalf("unsafe extension falls back to .bin: %s", path)
	}
}

func TestPlanVersionsAndIndex(t *testing.T) {
	store := openTestStore(t)
	sess, _ := store.CreateSession("versions")
	base := time.Now()
	for v := 1; v <= 2; v++ {
		p := &Plan{ID: "p1", SessionID: sess.ID, Version: v, Title: "React", CreatedAt: base}
		if err := store.SavePlan(p); err != nil {
			t.Fatalf("SavePlan v%d: %v", v, err)
		}
	}
	latest, err := store.Plan("p1")
	if err != nil || latest.Version != 2 {
		t.Fatalf("latest plan: %v %+v", err, latest)
	}
	v1, err := store.PlanVersion("p1", 1)
	if err != nil || v1.Version != 1 {
		t.Fatalf("older version kept: %v", err)
	}
	plans, err := store.Plans()
	if err != nil || len(plans) != 1 || plans[0].Version != 2 {
		t.Fatalf("index tracks latest: %v %+v", err, plans)
	}
}

func TestLearningDataRoundTrips(t *testing.T) {
	store := openTestStore(t)
	plan := &Plan{ID: "p1", SessionID: "s1", Version: 1, CreatedAt: time.Now()}
	if err := store.SavePlan(plan); err != nil {
		t.Fatalf("SavePlan: %v", err)
	}

	quiz := Quiz{ID: "q1", PlanID: "p1", NodeID: "k1", Type: QuizChoice,
		Payload: QuizPayload{Question: "1+1?", Options: []string{"1", "2"}, Answer: "2"}}
	if err := store.SaveQuizzes("p1", []Quiz{quiz}); err != nil {
		t.Fatalf("SaveQuizzes: %v", err)
	}
	quizzes, _ := store.Quizzes("p1")
	if len(quizzes) != 1 || quizzes[0].Payload.Answer != "2" {
		t.Fatalf("quizzes persist: %+v", quizzes)
	}

	correct := true
	if err := store.AppendAttempt("p1", QuizAttempt{QuizID: "q1", Selected: "2", Correct: &correct, At: time.Now()}); err != nil {
		t.Fatalf("AppendAttempt: %v", err)
	}
	attempts, _ := store.Attempts("p1")
	if len(attempts) != 1 || !*attempts[0].Correct {
		t.Fatalf("attempts persist: %+v", attempts)
	}

	if err := store.SaveProgress("p1", []Progress{{NodeID: "k1", Status: ProgressLearning, Mastery: 0.5, UpdatedAt: time.Now()}}); err != nil {
		t.Fatalf("SaveProgress: %v", err)
	}
	progress, _ := store.ProgressList("p1")
	if len(progress) != 1 || progress[0].Mastery != 0.5 {
		t.Fatalf("progress persists: %+v", progress)
	}

	log := &LearningLog{PlanID: "p1", Date: "2026-10-14", Minutes: 45}
	if err := store.AddLog(log); err != nil {
		t.Fatalf("AddLog: %v", err)
	}
	if log.ID == "" {
		t.Fatalf("log id minted")
	}
	logs, _ := store.Logs("p1")
	if len(logs) != 1 {
		t.Fatalf("logs persist: %+v", logs)
	}
	if err := store.DeleteLog("p1", log.ID); err != nil {
		t.Fatalf("DeleteLog: %v", err)
	}
	logs, _ = store.Logs("p1")
	if len(logs) != 0 {
		t.Fatalf("log deleted: %+v", logs)
	}
	if err := store.DeleteLog("p1", "ghost"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing log delete is ErrNotFound: %v", err)
	}
}

func TestNotesLifecycle(t *testing.T) {
	store := openTestStore(t)
	note := &Note{Title: "想法", Body: "# 内容"}
	if err := store.SaveNote(note); err != nil {
		t.Fatalf("SaveNote: %v", err)
	}
	if note.ID == "" {
		t.Fatalf("note id minted")
	}
	got, err := store.Note(note.ID)
	if err != nil || got.Title != "想法" {
		t.Fatalf("Note: %v %+v", err, got)
	}
	got.Body = "更新"
	if err := store.SaveNote(got); err != nil {
		t.Fatalf("SaveNote update: %v", err)
	}
	list, err := store.Notes()
	if err != nil || len(list) != 1 || list[0].Body != "更新" {
		t.Fatalf("Notes: %v %+v", err, list)
	}
	if err := store.DeleteNote(note.ID); err != nil {
		t.Fatalf("DeleteNote: %v", err)
	}
	if _, err := store.Note(note.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("note gone: %v", err)
	}
}
