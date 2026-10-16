package tutor

import (
	"bytes"
	"errors"
	"strings"
	"testing"
	"time"
)

// seedBackupStore fills a store with one of everything a backup must carry.
func seedBackupStore(t *testing.T, svc *Service) (sessionID, planID string) {
	t.Helper()
	sess, err := svc.store.CreateSession("备份源会话")
	if err != nil {
		t.Fatal(err)
	}
	mat := &Material{ID: newID(), SessionID: sess.ID, Kind: MaterialFile, Source: "notes.pdf", Status: "parsed", Chars: 9, CreatedAt: time.Now()}
	if err := svc.store.SaveMaterial(mat); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.WriteMaterialParsed(sess.ID, mat.ID, "解析出的正文"); err != nil {
		t.Fatal(err)
	}
	plan := &Plan{
		ID: newID(), SessionID: sess.ID, Version: 2,
		Title: "Rust 进阶", Topic: "Rust", CreatedAt: time.Date(2026, 10, 1, 8, 0, 0, 0, time.UTC),
		Intent:     Intent{Topic: "Rust", Goal: "进阶"},
		Knowledge:  KnowledgeMap{Nodes: []KnowledgeNode{{SID: "n1", Name: "生命周期", Difficulty: 4, MustLearn: true}}},
		Stages:     []Stage{{Index: 1, Name: "深入类型", Goal: "trait 与泛型", Difficulty: 4}},
		Schedule:   []ScheduleDay{{Date: "2026-10-02", StageIndex: 1, Tasks: []string{"读 trait 章节"}, EstMinutes: 45}},
		Validation: &ValidationReport{Passed: true},
	}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	sess.PlanID = plan.ID
	if err := svc.store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}
	quiz := Quiz{ID: newID(), PlanID: plan.ID, NodeID: "n1", Type: QuizChoice,
		Payload: QuizPayload{Question: "生命周期标注用什么符号？", Options: []string{"'", "&", "*"}, Answer: "&"}}
	if err := svc.store.SaveQuizzes(plan.ID, []Quiz{quiz}); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.AppendAttempt(plan.ID, QuizAttempt{QuizID: quiz.ID, Selected: "&", Correct: new(true), At: time.Now()}); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.SaveProgress(plan.ID, []Progress{{NodeID: "n1", Status: ProgressDone, Mastery: 0.8, UpdatedAt: time.Now()}}); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.AddLog(&LearningLog{PlanID: plan.ID, Date: "2026-10-02", Minutes: 45, Note: "第一课"}); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.SaveStudyUnits(plan.ID, []StudyUnit{{ID: newID(), Order: 1, Title: "trait 详解", StageIndex: 1, NodeSIDs: []string{"n1"}, ContentMD: "# trait"}}); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.SaveNote(&Note{Title: "备份笔记", Body: "内容"}); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.KbAddText("备份文档", "知识库备份测试正文"); err != nil {
		t.Fatal(err)
	}
	return sess.ID, plan.ID
}

func TestBackupRoundTrip(t *testing.T) {
	src := newShareService(t)
	srcSession, srcPlan := seedBackupStore(t, src)

	doc, err := src.BuildBackup()
	if err != nil {
		t.Fatal(err)
	}
	if doc.Format != backupFormat || doc.Version != backupVersion {
		t.Fatalf("doc header: %+v", doc)
	}
	var buf bytes.Buffer
	if err := WriteBackup(&buf, doc); err != nil {
		t.Fatal(err)
	}
	parsed, err := ParseBackup(buf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	preview := BackupPreview(parsed)
	if preview["sessions"] != 1 || preview["materials"] != 1 || preview["plans"] != 1 ||
		preview["quizzes"] != 1 || preview["attempts"] != 1 || preview["progress"] != 1 ||
		preview["learning_logs"] != 1 || preview["study_units"] != 1 || preview["notes"] != 1 ||
		preview["kb_documents"] != 1 || preview["knowledge_nodes"] != 1 {
		t.Fatalf("preview counts: %v", preview)
	}

	// Restore into a fresh store: append-only, fresh ids, full payloads.
	dst := newShareService(t)
	summary, err := dst.RestoreBackup(parsed)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"sessions", "materials", "plans", "knowledge_nodes", "quizzes", "attempts", "progress", "learning_logs", "study_units", "notes", "kb_documents"} {
		if summary[key] != 1 {
			t.Fatalf("summary[%s] = %v, want 1 (%v)", key, summary[key], summary)
		}
	}

	sessions, _ := dst.store.Sessions()
	if len(sessions) != 1 || sessions[0].Title != "备份源会话" {
		t.Fatalf("restored sessions: %+v", sessions)
	}
	newSess := sessions[0]
	if newSess.ID == srcSession {
		t.Fatal("restored session must get a fresh id")
	}
	mats, _ := dst.store.Materials(newSess.ID)
	if len(mats) != 1 || mats[0].Status != "parsed" {
		t.Fatalf("restored materials: %+v", mats)
	}
	if text, err := dst.store.ReadMaterialParsed(newSess.ID, mats[0].ID); err != nil || text != "解析出的正文" {
		t.Fatalf("restored parsed text: %q %v", text, err)
	}
	newPlanID := newSess.PlanID
	if newPlanID == "" || newPlanID == srcPlan {
		t.Fatal("session should point at a fresh plan id")
	}
	plan, err := dst.store.Plan(newPlanID)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Title != "Rust 进阶" || plan.Version != 2 || len(plan.Knowledge.Nodes) != 1 || plan.Validation == nil || !plan.Validation.Passed {
		t.Fatalf("restored plan mismatch: %+v", plan)
	}
	quizzes, _ := dst.store.Quizzes(newPlanID)
	if len(quizzes) != 1 {
		t.Fatalf("restored quizzes: %+v", quizzes)
	}
	if quizzes[0].Payload.Answer != "&" {
		t.Fatalf("a backup must keep the answer for re-grading: %+v", quizzes[0].Payload)
	}
	attempts, _ := dst.store.Attempts(newPlanID)
	if len(attempts) != 1 || attempts[0].QuizID != quizzes[0].ID {
		t.Fatalf("attempts must remap to the new quiz id: %+v vs %+v", attempts, quizzes)
	}
	progress, _ := dst.store.ProgressList(newPlanID)
	if len(progress) != 1 || progress[0].NodeID != "n1" || progress[0].Mastery != 0.8 {
		t.Fatalf("restored progress: %+v", progress)
	}
	logs, _ := dst.store.Logs(newPlanID)
	if len(logs) != 1 || logs[0].Minutes != 45 {
		t.Fatalf("restored logs: %+v", logs)
	}
	units, _ := dst.store.StudyUnits(newPlanID)
	if len(units) != 1 || units[0].ContentMD != "# trait" {
		t.Fatalf("restored units: %+v", units)
	}
	notes, _ := dst.store.Notes()
	if len(notes) != 1 || notes[0].Title != "备份笔记" {
		t.Fatalf("restored notes: %+v", notes)
	}
	docs, _ := dst.store.KbDocs()
	if len(docs) != 1 || docs[0].Title != "备份文档" {
		t.Fatalf("restored kb docs: %+v", docs)
	}
	if text, err := dst.store.ReadKbParsed(docs[0].ID); err != nil || text != "知识库备份测试正文" {
		t.Fatalf("restored kb text: %q %v", text, err)
	}
}

func TestBackupImportIsAppendAndDedup(t *testing.T) {
	src := newShareService(t)
	seedBackupStore(t, src)
	doc, err := src.BuildBackup()
	if err != nil {
		t.Fatal(err)
	}
	dst := newShareService(t)
	seedBackupStore(t, dst) // same content hashes in both stores
	for range 2 {
		if _, err := dst.RestoreBackup(doc); err != nil {
			t.Fatal(err)
		}
	}
	// Two imports double the rows (append semantics) except knowledge-base
	// documents, which dedup by content hash.
	if sessions, _ := dst.store.Sessions(); len(sessions) != 3 {
		t.Fatalf("append should add sessions: %d", len(sessions))
	}
	if docs, _ := dst.store.KbDocs(); len(docs) != 1 {
		t.Fatalf("kb dedup should hold across imports: %d", len(docs))
	}
	if notes, _ := dst.store.Notes(); len(notes) != 3 {
		t.Fatalf("append should add notes: %d", len(notes))
	}
}

func TestBackupParseGuards(t *testing.T) {
	if _, err := ParseBackup([]byte(`{"format":"other","version":1}`)); !errors.Is(err, ErrBadBackup) {
		t.Fatalf("format guard: %v", err)
	}
	if _, err := ParseBackup([]byte(`{"format":"` + backupFormat + `","version":99}`)); err == nil || !strings.Contains(err.Error(), "version") {
		t.Fatalf("version guard: %v", err)
	}
	if _, err := ParseBackup([]byte(`not json`)); !errors.Is(err, ErrBadBackup) {
		t.Fatalf("json guard: %v", err)
	}
}

func TestBackupQuotaSkips(t *testing.T) {
	svc := newShareService(t)
	doc := &BackupDoc{Format: backupFormat, Version: backupVersion}
	for i := range noteQuota + 2 {
		doc.Notes = append(doc.Notes, BackupNote{Title: strings.Repeat("n", 1+i%3), Body: "b"})
	}
	summary, err := svc.RestoreBackup(doc)
	if err != nil {
		t.Fatal(err)
	}
	notes, _ := svc.store.Notes()
	if len(notes) != noteQuota || summary["notes"] != noteQuota {
		t.Fatalf("note quota: %d restored, summary %v", len(notes), summary["notes"])
	}
	skipped := summary["skipped"].([]string)
	if len(skipped) == 0 || !strings.Contains(skipped[0], "笔记数达上限") {
		t.Fatalf("skipped accounting: %v", skipped)
	}
}
