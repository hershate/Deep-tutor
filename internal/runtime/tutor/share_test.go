package tutor

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// seedSharedPlan writes a plan with one sanitized-carrying resource and one
// quiz with an answer, returning its id.
func seedSharedPlan(t *testing.T, svc *Service, resourceURL string) string {
	t.Helper()
	sess, err := svc.store.CreateSession("share-source")
	if err != nil {
		t.Fatal(err)
	}
	plan := &Plan{
		ID: newID(), SessionID: sess.ID, Version: 1,
		Title: "Rust 入门", Topic: "Rust", CreatedAt: time.Now(), UpdatedAt: time.Now(),
		Intent: Intent{Topic: "Rust", Level: "beginner", Goal: "入门 Rust"},
		Stages: []Stage{{
			Index: 1, Name: "所有权", Goal: "理解所有权与借用", Difficulty: 3,
			Topics:    []StageTopic{{Name: "ownership", Why: "核心心智模型"}},
			Resources: []Resource{{StageIndex: 1, Title: "Rust 手册", URL: resourceURL, Type: ResourceDoc, Verified: VerifyOK}},
		}},
	}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.SaveQuizzes(plan.ID, []Quiz{{
		ID: newID(), PlanID: plan.ID, NodeID: "n1", Type: QuizChoice,
		Payload: QuizPayload{Question: "Rust 的所有权规则有几条？", Options: []string{"2", "3", "4"}, Answer: "3", Explanation: "所有权/移动/借用"},
	}}); err != nil {
		t.Fatal(err)
	}
	return plan.ID
}

func TestShareCreateIdempotentRevoke(t *testing.T) {
	svc := newShareService(t)
	planID := seedSharedPlan(t, svc, "https://doc.rust-lang.org/book/")

	sh, err := svc.SharePlan(planID)
	if err != nil {
		t.Fatal(err)
	}
	if len(sh.Token) < 30 || strings.ContainsAny(sh.Token, "/+=") {
		t.Fatalf("token should be long url-safe base64: %q", sh.Token)
	}
	again, err := svc.SharePlan(planID)
	if err != nil {
		t.Fatal(err)
	}
	if again.Token != sh.Token {
		t.Fatalf("second create must return the existing share: %q vs %q", again.Token, sh.Token)
	}

	status, err := svc.ShareStatus(planID)
	if err != nil || status == nil || status.Token != sh.Token {
		t.Fatalf("status mismatch: %v %v", status, err)
	}
	entries, err := svc.ShareList()
	if err != nil || len(entries) != 1 || entries[0].Title != "Rust 入门" {
		t.Fatalf("share list mismatch: %+v %v", entries, err)
	}

	if err := svc.ShareRevoke(planID); err != nil {
		t.Fatal(err)
	}
	if err := svc.ShareRevoke(planID); err != nil {
		t.Fatalf("revoking an unshared plan must be a no-op: %v", err)
	}
	if status, err := svc.ShareStatus(planID); err != nil || status != nil {
		t.Fatalf("revoked share should be gone: %v %v", status, err)
	}
	if entries, err := svc.ShareList(); err != nil || len(entries) != 0 {
		t.Fatalf("revoked share should leave the list: %+v %v", entries, err)
	}
	if _, err := svc.SharedPlanView(sh.Token); !errors.Is(err, ErrNotFound) {
		t.Fatalf("revoked token must read as not found: %v", err)
	}
}

func TestSharePlanMissing(t *testing.T) {
	svc := newShareService(t)
	if _, err := svc.SharePlan("nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
	if _, err := svc.ShareStatus("nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
	if err := svc.ShareRevoke("nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestSharedPlanViewSanitizes(t *testing.T) {
	svc := newShareService(t)
	planID := seedSharedPlan(t, svc, "javascript:alert(1)")
	sh, err := svc.SharePlan(planID)
	if err != nil {
		t.Fatal(err)
	}
	view, err := svc.SharedPlanView(sh.Token)
	if err != nil {
		t.Fatal(err)
	}
	if view.Title != "Rust 入门" || len(view.Stages) != 1 {
		t.Fatalf("view shape mismatch: %+v", view)
	}
	res := view.Stages[0].Resources[0]
	if res.URL != "" || res.Title != "Rust 手册" {
		t.Fatalf("non-http(s) resource URL must be blanked: %+v", res)
	}
	if len(view.Quizzes) != 1 {
		t.Fatalf("quiz count mismatch: %+v", view.Quizzes)
	}
	q := view.Quizzes[0]
	if q.Question == "" || len(q.Options) != 3 {
		t.Fatalf("quiz question side must survive: %+v", q)
	}
	if _, err := svc.SharedPlanView("bogus"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown token: %v", err)
	}
}

func TestSharedPlanViewHTTPLinkSurvives(t *testing.T) {
	svc := newShareService(t)
	planID := seedSharedPlan(t, svc, "HTTPS://doc.rust-lang.org/book/")
	sh, err := svc.SharePlan(planID)
	if err != nil {
		t.Fatal(err)
	}
	view, err := svc.SharedPlanView(sh.Token)
	if err != nil {
		t.Fatal(err)
	}
	if view.Stages[0].Resources[0].URL != "HTTPS://doc.rust-lang.org/book/" {
		t.Fatalf("http(s) URL must survive verbatim: %+v", view.Stages[0].Resources[0])
	}
}

func TestShareOrphanSelfHeals(t *testing.T) {
	svc := newShareService(t)
	planID := seedSharedPlan(t, svc, "https://example.com")
	sh, err := svc.SharePlan(planID)
	if err != nil {
		t.Fatal(err)
	}
	// Link the session row to its plan the way the pipeline does, so the
	// delete cascade owns both.
	sessID := mustSessionID(t, svc, planID)
	sess, err := svc.store.Session(sessID)
	if err != nil {
		t.Fatal(err)
	}
	sess.PlanID = planID
	if err := svc.store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}
	if err := svc.store.DeleteSession(sessID); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.SharedPlanView(sh.Token); !errors.Is(err, ErrNotFound) {
		t.Fatalf("share over a deleted plan must read as not found: %v", err)
	}
	if entries, err := svc.ShareList(); err != nil || len(entries) != 0 {
		t.Fatalf("cascade should drop the share: %+v %v", entries, err)
	}
}

func newShareService(t *testing.T) *Service {
	t.Helper()
	store, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return NewService(store, ServiceOptions{})
}

func mustSessionID(t *testing.T, svc *Service, planID string) string {
	t.Helper()
	plan, err := svc.store.Plan(planID)
	if err != nil {
		t.Fatal(err)
	}
	return plan.SessionID
}
