package serve

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/config"
	"reasonix/internal/runtime/tutor"
	"reasonix/internal/session/control"
)

func newShareTestServer(t *testing.T) (*httptest.Server, *tutor.Store) {
	t.Helper()
	root := t.TempDir()
	store, err := tutor.OpenStore(filepath.Join(root, "tutor"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		storeRoot := filepath.Join(root, "tutor")
		for range 20 {
			if err := os.RemoveAll(storeRoot); err == nil {
				return
			}
			time.Sleep(25 * time.Millisecond)
		}
	})
	svc := tutor.NewService(store, tutor.ServiceOptions{})
	ctrl := control.New(control.Options{})
	s := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	s.SetTutorService(svc)
	srv := httptest.NewServer(s.routes())
	t.Cleanup(srv.Close)
	return srv, store
}

func TestTutorShareEndpoints(t *testing.T) {
	srv, store := newShareTestServer(t)

	sess, err := store.CreateSession("share-flow")
	if err != nil {
		t.Fatal(err)
	}
	plan := &tutor.Plan{
		ID: "plan1", SessionID: sess.ID, Version: 1,
		Title: "Rust 入门方案", Topic: "Rust", CreatedAt: time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC),
		Intent:    tutor.Intent{Topic: "Rust", Level: "beginner", Goal: "掌握所有权", DailyMinutes: 30, Deadline: "2026-12-01"},
		Knowledge: tutor.KnowledgeMap{Nodes: []tutor.KnowledgeNode{{SID: "n1", Name: "所有权", Difficulty: 3, MustLearn: true}}},
		Stages: []tutor.Stage{{
			Index: 1, Name: "第一阶段", Goal: "理解所有权", Difficulty: 2, Milestone: "能解释借用检查",
			Resources: []tutor.Resource{
				{StageIndex: 1, Title: "Rust 手册", URL: "https://doc.rust-lang.org/book/", Type: tutor.ResourceDoc, Verified: tutor.VerifyOK},
				{StageIndex: 1, Title: "危险链接", URL: "javascript:alert(1)", Type: tutor.ResourceVideo, Verified: tutor.VerifyUncertain},
			},
		}},
		Schedule: []tutor.ScheduleDay{{Date: "2026-10-15", StageIndex: 1, Tasks: []string{"读第 4 章"}, EstMinutes: 30}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveQuizzes(plan.ID, []tutor.Quiz{{
		ID: "q1", PlanID: plan.ID, NodeID: "n1", Type: tutor.QuizChoice,
		Payload: tutor.QuizPayload{Question: "所有权的规则是什么？", Options: []string{"规则A", "规则B"}, Answer: "规则A", Explanation: "唯一答案解释标记XYZ"},
	}}); err != nil {
		t.Fatal(err)
	}
	sess.PlanID = plan.ID
	if err := store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}

	// Create, twice: the second call returns the same token.
	resp, body := tutorPost(t, srv, "/tutor/plans/plan1/share", nil)
	if resp.StatusCode != http.StatusCreated || !strings.Contains(body, `"shared":true`) || !strings.Contains(body, `"path":"/tutor/shared/`) {
		t.Fatalf("create share: %d %s", resp.StatusCode, body)
	}
	token := tutorJSONString(t, body, "token")
	resp, body = tutorPost(t, srv, "/tutor/plans/plan1/share", nil)
	if resp.StatusCode != http.StatusCreated || tutorJSONString(t, body, "token") != token {
		t.Fatalf("second create must return the same share: %d %s", resp.StatusCode, body)
	}

	// Status and the management list both see it.
	resp, body = tutorGet(t, srv, "/tutor/plans/plan1/share")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, token) {
		t.Fatalf("share status: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/shares")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "Rust 入门方案") || !strings.Contains(body, token) {
		t.Fatalf("share list: %d %s", resp.StatusCode, body)
	}

	// The public page renders the plan without the answer side or the
	// crafted scheme, and keeps the http(s) link clickable.
	resp, body = tutorGet(t, srv, "/tutor/shared/"+token)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("public page: %d %s", resp.StatusCode, body)
	}
	for _, want := range []string{"Rust 入门方案", "掌握所有权", "所有权", "href=\"https://doc.rust-lang.org/book/\"", "所有权的规则是什么？", "规则A", "答案不随分享提供"} {
		if !strings.Contains(body, want) {
			t.Fatalf("public page missing %q", want)
		}
	}
	for _, banned := range []string{"javascript:", "唯一答案解释标记XYZ"} {
		if strings.Contains(body, banned) {
			t.Fatalf("public page leaked %q", banned)
		}
	}

	// JSON form for programmatic readers.
	resp, body = tutorGet(t, srv, "/tutor/shared/"+token+"?format=json")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"question"`) || strings.Contains(body, "唯一答案解释标记XYZ") {
		t.Fatalf("json view: %d %s", resp.StatusCode, body)
	}

	// Revoke, then the token is gone and status reports unshared.
	resp, _ = tutorDelete(t, srv, "/tutor/plans/plan1/share")
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("revoke: %d", resp.StatusCode)
	}
	resp, body = tutorGet(t, srv, "/tutor/plans/plan1/share")
	if resp.StatusCode != http.StatusOK || strings.Contains(body, token) {
		t.Fatalf("status after revoke: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/shared/"+token)
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(body, "分享链接不存在") {
		t.Fatalf("revoked token page: %d %s", resp.StatusCode, body)
	}

	// Unknown plan answers the tutor refusal, not the public page.
	resp, body = tutorPost(t, srv, "/tutor/plans/ghost/share", nil)
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(body, "tutor.not_found") {
		t.Fatalf("share of missing plan: %d %s", resp.StatusCode, body)
	}
}

// tutorJSONString pulls one top-level string field out of a small response.
func tutorJSONString(t *testing.T, body, field string) string {
	t.Helper()
	quote := `"` + field + `":"`
	i := strings.Index(body, quote)
	if i < 0 {
		t.Fatalf("field %s missing from %s", field, body)
	}
	rest := body[i+len(quote):]
	if before, _, ok := strings.Cut(rest, `"`); ok {
		return before
	}
	t.Fatalf("unterminated field %s in %s", field, body)
	return ""
}

func tutorDelete(t *testing.T, srv *httptest.Server, path string) (*http.Response, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodDelete, srv.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp, string(raw)
}
