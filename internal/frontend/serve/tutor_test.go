package serve

import (
	"archive/zip"
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
	"reasonix/internal/runtime/tutor"
	"reasonix/internal/session/control"
)

// tutorFakeLLM answers the pipeline's five JSON tools with fixed payloads.
type tutorFakeLLM struct{ calls int }

func (f *tutorFakeLLM) Name() string { return "fake" }

func (f *tutorFakeLLM) Stream(_ context.Context, req provider.Request) (<-chan provider.Chunk, error) {
	f.calls++
	system := req.Messages[0].Content
	reply := "{}"
	switch {
	case strings.Contains(system, "Intent"):
		reply = `{"topic":"React","level":"零基础","goal":"独立开发","daily_minutes":60,"needs_clarify":false}`
	case strings.Contains(system, "Knowledge Map"):
		reply = `{"nodes":[{"id":"k1","name":"JSX 基础","difficulty":2,"must_learn":true},
		{"id":"k2","name":"组件与状态","difficulty":3,"must_learn":true}],
		"edges":[]}`
	case strings.Contains(system, "Stages"):
		reply = `{"stages":[
		{"index":0,"name":"入门","goal":"基础","difficulty":1,"estimated_weeks":1,
		 "topics":[{"name":"JSX 基础","why":"地基","must_learn":true}],"milestone":"渲染组件"},
		{"index":1,"name":"进阶","goal":"状态","difficulty":3,"estimated_weeks":1,
		 "topics":[{"name":"组件与状态","why":"交互","must_learn":true}],"milestone":"计数器"},
		{"index":2,"name":"实战","goal":"应用","difficulty":5,"estimated_weeks":1,
		 "topics":[{"name":"路由","why":"应用形态","must_learn":true}],"milestone":"小应用"}]}`
	case strings.Contains(system, "学习排期"):
		reply = `{"schedule":[{"date":"2026-10-14","stage_index":0,"tasks":["学 JSX"],"est_minutes":60}]}`
	case strings.Contains(system, "Validate"):
		reply = `{"report":{"dependency_closure":{"status":"pass"},"difficulty_step":{"status":"pass"},
		"cognitive_load":{"status":"pass"},"milestone_feasibility":{"status":"pass"},"gap_analysis":{"status":"pass"}},
		"passed":true}`
	case strings.Contains(system, "UnitContentSpec"):
		reply = `{"objectives":["能解释基础"],"prerequisites":["无"],"estimated_time":"45 min","content_md":"### 概述

单元正文。"}`
	case strings.Contains(system, "Quiz"):
		reply = `{"title":"测验","quizzes":[
		{"node_id":"k1","type":"choice","question":"表达式用什么包裹？","options":["{ }","[ ]","( )","< >"],"answer":"{ }"},
		{"node_id":"k2","type":"choice","question":"状态更新函数？","options":["setState","setstate","updateState","changeState"],"answer":"setState"},
		{"node_id":"k1","type":"fill","question":"组件名以大写______开头。","answer":"字母"},
		{"node_id":"k2","type":"short","question":"受控组件？","answer":"state 驱动"},
		{"node_id":"","type":"choice","question":"JSX 是什么？","options":["语法扩展","数据库","协议","操作系统"],"answer":"语法扩展"}]}`
	case strings.Contains(system, "知识库问答"):
		// Echo the first context doc id back as the citation, the way an
		// honest kb_answer names its source.
		reply = `{"answer":"据文档回答。","used_doc_ids":[]}`
		if len(req.Messages) > 1 {
			user := req.Messages[1].Content
			if _, rest, ok := strings.Cut(user, `"doc_id":"`); ok {
				if docID, _, found := strings.Cut(rest, `"`); found && docID != "" {
					reply = `{"answer":"据文档回答。","used_doc_ids":["` + docID + `"]}`
				}
			}
		}
	}
	ch := make(chan provider.Chunk, 2)
	ch <- provider.Chunk{Type: provider.ChunkText, Text: reply}
	ch <- provider.Chunk{Type: provider.ChunkUsage, Usage: &provider.Usage{PromptTokens: 10, CompletionTokens: 5}}
	close(ch)
	return ch, nil
}

func newTutorTestServer(t *testing.T) (*Server, *httptest.Server, *tutorFakeLLM) {
	t.Helper()
	root := t.TempDir()
	store, err := tutor.OpenStore(filepath.Join(root, "tutor"))
	if err != nil {
		t.Fatal(err)
	}
	// Clear the store ourselves before t.TempDir's single-shot remove: on
	// Windows it races just-ended renames with "directory not empty".
	t.Cleanup(func() {
		storeRoot := filepath.Join(root, "tutor")
		for range 20 {
			if err := os.RemoveAll(storeRoot); err == nil {
				return
			}
			time.Sleep(25 * time.Millisecond)
		}
	})
	llm := &tutorFakeLLM{}
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	svc := tutor.NewService(store, tutor.ServiceOptions{
		Provider: func() (tutor.LLM, error) { return llm, nil },
		Now:      func() time.Time { return now },
	})
	ctrl := control.New(control.Options{})
	s := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	s.SetTutorService(svc)
	srv := httptest.NewServer(s.routes())
	t.Cleanup(srv.Close)
	return s, srv, llm
}

func tutorPost(t *testing.T, srv *httptest.Server, path string, body any) (*http.Response, string) {
	t.Helper()
	var reader *strings.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = strings.NewReader(string(raw))
	} else {
		reader = strings.NewReader("{}")
	}
	resp, err := http.Post(srv.URL+path, "application/json", reader)
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

// tutorPostSSE posts and hands the response straight to the SSE reader; the
// body must not be buffered first.
func tutorPostSSE(t *testing.T, srv *httptest.Server, path string, body any) *http.Response {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.Post(srv.URL+path, "application/json", strings.NewReader(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func tutorGet(t *testing.T, srv *httptest.Server, path string) (*http.Response, string) {
	t.Helper()
	resp, err := http.Get(srv.URL + path)
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

// readSSE collects the event/data pairs until the stream ends, which the
// relay guarantees happens on the terminal event.
func readSSE(t *testing.T, resp *http.Response) []map[string]string {
	t.Helper()
	var events []map[string]string
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 1<<20), 1<<20)
	current := map[string]string{}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if len(current) > 0 {
				events = append(events, current)
				if current["event"] == "done" || current["event"] == "error" || current["event"] == "clarify_needed" {
					return events
				}
				current = map[string]string{}
			}
			continue
		}
		for _, prefix := range []string{"id: ", "event: ", "data: "} {
			if rest, ok := strings.CutPrefix(line, prefix); ok {
				current[strings.TrimSuffix(prefix, ": ")] = rest
			}
		}
	}
	return events
}

// An empty tutor store still owes the page arrays: the sheet dereferences
// every list without a null check, and JSON null blanks the whole surface.
func TestTutorEmptyListsAreArrays(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	for path, want := range map[string]string{
		"/tutor/plans":                 "[]",
		"/tutor/notes":                 "[]",
		"/tutor/progress/logs":         "[]",
		"/tutor/progress/review/due":   `{"items":`,
		"/tutor/progress/review/queue": `{"items":`,
	} {
		resp, body := tutorGet(t, srv, path)
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s: %d %s", path, resp.StatusCode, body)
		}
		if strings.Contains(body, "null") || !strings.HasPrefix(body, want) {
			t.Fatalf("%s: empty store must answer %q without null, got %s", path, want, body)
		}
	}
}

// An export with nothing to carry — an empty library, or a filter that
// matches nothing — refuses cleanly; a bare 500 here once leaked through.
func TestTutorKbExportEmptyRefuses(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	for _, path := range []string{"/tutor/kb/export", "/tutor/kb/export?ids=nope"} {
		resp, body := tutorGet(t, srv, path)
		if resp.StatusCode != http.StatusBadRequest || !strings.Contains(body, "tutor.kb_export_empty") {
			t.Fatalf("%s: %d %s", path, resp.StatusCode, body)
		}
	}
}

func TestTutorEndpointsEndToEnd(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)

	// Session lifecycle.
	resp, body := tutorPost(t, srv, "/tutor/sessions", map[string]string{"title": ""})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create session: %d %s", resp.StatusCode, body)
	}
	var session struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(body), &session); err != nil {
		t.Fatal(err)
	}
	resp, body = tutorGet(t, srv, "/tutor/sessions")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, session.ID) {
		t.Fatalf("list sessions: %d %s", resp.StatusCode, body)
	}

	// Materials: text in, confirmed, parsed.
	resp, body = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/materials/text",
		map[string]string{"name": "笔记", "content": "React 是一个 UI 库。"})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("material text: %d %s", resp.StatusCode, body)
	}
	var material struct {
		ID string `json:"id"`
	}
	json.Unmarshal([]byte(body), &material)
	resp, body = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/materials/"+material.ID+"/confirm",
		map[string]string{"parse_mode": "full"})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("confirm: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/materials/"+material.ID+"/parse", nil)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"parsed"`) {
		t.Fatalf("parse: %d %s", resp.StatusCode, body)
	}

	// Generate: the SSE stream carries the pipeline's events to done.
	respSSE := tutorPostSSE(t, srv, "/tutor/sessions/"+session.ID+"/generate",
		map[string]any{"raw_text": "两周学会 React", "daily_minutes": 60})
	if respSSE.StatusCode != http.StatusOK {
		t.Fatalf("generate: %d", respSSE.StatusCode)
	}
	events := readSSE(t, respSSE)
	if len(events) == 0 || events[len(events)-1]["event"] != "done" {
		t.Fatalf("generate terminal = %+v", events[len(events)-1])
	}
	starts := 0
	for _, e := range events {
		if e["event"] == "tool_start" {
			starts++
		}
	}
	if starts != 7 {
		t.Fatalf("tool_starts = %d, want 7 (parse injected)", starts)
	}
	var done struct {
		PlanID string `json:"plan_id"`
	}
	if err := json.Unmarshal([]byte(events[len(events)-1]["data"]), &done); err != nil {
		t.Fatal(err)
	}
	if done.PlanID == "" {
		t.Fatalf("done carries the plan id: %s", events[len(events)-1]["data"])
	}

	// A refresh replays the terminal instead of re-billing.
	replayResp := tutorPostSSE(t, srv, "/tutor/sessions/"+session.ID+"/generate",
		map[string]any{"raw_text": "两周学会 React"})
	if replayResp.StatusCode != http.StatusOK {
		t.Fatalf("replay generate: %d", replayResp.StatusCode)
	}
	replay := readSSE(t, replayResp)
	if len(replay) != 1 || replay[0]["event"] != "done" {
		t.Fatalf("terminal replay = %+v", replay)
	}

	// Plans.
	resp, body = tutorGet(t, srv, "/tutor/plans")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, done.PlanID) {
		t.Fatalf("plans: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/plans/"+done.PlanID)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"React"`) {
		t.Fatalf("plan: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/plans/"+done.PlanID+"/export.md")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "React") {
		t.Fatalf("export: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/plans/"+done.PlanID+"/export.ics")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "BEGIN:VCALENDAR") {
		t.Fatalf("export.ics: %d %s", resp.StatusCode, body)
	}

	// Study materials: SSE stream then idempotent JSON snapshot.
	req, err := http.NewRequest(http.MethodPost, srv.URL+"/tutor/plans/"+done.PlanID+"/materials", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Accept", "text/event-stream")
	sseResp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	sseBody, _ := io.ReadAll(sseResp.Body)
	sseResp.Body.Close()
	stream := string(sseBody)
	if !strings.Contains(stream, "event: unit_start") || !strings.Contains(stream, "event: unit_done") || !strings.Contains(stream, "event: done") {
		t.Fatalf("materials SSE stream missing events:\n%s", stream)
	}
	resp, body = tutorPost(t, srv, "/tutor/plans/"+done.PlanID+"/materials", nil)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "unit_count") || !strings.Contains(body, "自学路线图") {
		t.Fatalf("materials json: %d %s", resp.StatusCode, body)
	}

	// Quizzes: generate, grade, wrong book.
	resp, body = tutorPost(t, srv, "/tutor/plans/"+done.PlanID+"/quiz", nil)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"quiz_count":5`) {
		t.Fatalf("quiz: %d %s", resp.StatusCode, body)
	}
	var set struct {
		Quizzes []struct {
			ID       string `json:"id"`
			Question string `json:"question"`
		} `json:"quizzes"`
	}
	json.Unmarshal([]byte(body), &set)
	var right, wrong string
	for _, q := range set.Quizzes {
		switch q.Question {
		case "表达式用什么包裹？":
			right = q.ID
		case "状态更新函数？":
			wrong = q.ID
		}
	}
	resp, body = tutorPost(t, srv, "/tutor/quizzes/"+right+"/submit",
		map[string]string{"plan_id": done.PlanID, "selected": "{ }"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"correct":true`) {
		t.Fatalf("submit right: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorPost(t, srv, "/tutor/quizzes/"+wrong+"/submit",
		map[string]string{"plan_id": done.PlanID, "selected": "setstate"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"correct":false`) {
		t.Fatalf("submit wrong: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/quizzes/wrong-overview")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"total":1`) {
		t.Fatalf("wrong overview: %d %s", resp.StatusCode, body)
	}

	// Progress and review.
	resp, body = tutorPost(t, srv, "/tutor/progress",
		map[string]any{"plan_id": done.PlanID, "node_id": "k1", "status": "done"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"mastery":1`) {
		t.Fatalf("progress: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/plans/"+done.PlanID+"/progress")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "mastery_avg") {
		t.Fatalf("progress of: %d %s", resp.StatusCode, body)
	}

	// Logs and stats.
	resp, body = tutorPost(t, srv, "/tutor/progress/logs",
		map[string]any{"plan_id": done.PlanID, "minutes": 30})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("log add: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/progress/stats/summary")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"total_minutes":30`) {
		t.Fatalf("stats: %d %s", resp.StatusCode, body)
	}

	// Notes.
	resp, body = tutorPost(t, srv, "/tutor/notes", map[string]string{"title": "想法", "body": "# 标题"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"id"`) {
		t.Fatalf("note create: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/notes")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "想法") {
		t.Fatalf("notes: %d %s", resp.StatusCode, body)
	}

	// Global search: the plan topic and the note title both hit; the note
	// body hit carries a snippet.
	resp, body = tutorGet(t, srv, "/tutor/search?q=React&limit=5")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"plans":{"total":1`) {
		t.Fatalf("search plans: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/search?q="+url.QueryEscape("想法"))
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"notes":{"total":1`) {
		t.Fatalf("search notes: %d %s", resp.StatusCode, body)
	}

	// Usage summary: the ledger holds the run's cost.
	resp, body = tutorGet(t, srv, "/tutor/usage")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"today"`) || !strings.Contains(body, `"daily"`) {
		t.Fatalf("usage: %d %s", resp.StatusCode, body)
	}

	// Notes ZIP export, then import round-trip through base64-JSON.
	resp, body = tutorGet(t, srv, "/tutor/notes/export.zip")
	if resp.StatusCode != http.StatusOK || !strings.Contains(resp.Header.Get("Content-Type"), "application/zip") {
		t.Fatalf("notes zip: %d %s", resp.StatusCode, body)
	}
	zr, err := zip.NewReader(strings.NewReader(body), int64(len(body)))
	if err != nil {
		t.Fatalf("notes zip parse: %v", err)
	}
	if !slices.ContainsFunc(zr.File, func(f *zip.File) bool { return f.Name == "index.md" }) {
		t.Fatal("notes zip lacks index.md")
	}
	importBody := map[string]any{"files": []map[string]string{
		{"name": "export.zip", "content_base64": base64.StdEncoding.EncodeToString([]byte(body))},
	}}
	resp, body = tutorPost(t, srv, "/tutor/notes/import", importBody)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"imported":1`) {
		t.Fatalf("notes import: %d %s", resp.StatusCode, body)
	}
	_, body = tutorGet(t, srv, "/tutor/notes")
	if !strings.Contains(body, "想法") {
		t.Fatalf("imported note missing: %s", body)
	}
}

// TestTutorKbEndpoints walks the knowledge base end to end: staged upload
// through confirm/parse, text filing, list search, raw and parsed reads, the
// citation-backed chat with its records, ZIP export, one-click generation,
// and the search domain.
func TestTutorKbEndpoints(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)

	// Upload stages pending; the same bytes dedup onto the same row.
	upload := map[string]string{
		"filename":       "指南.md",
		"content_base64": base64.StdEncoding.EncodeToString([]byte("# 指南\n组件复用是 React 的核心。")),
	}
	resp, body := tutorPost(t, srv, "/tutor/kb/documents/upload", upload)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"status":"pending"`) {
		t.Fatalf("kb upload: %d %s", resp.StatusCode, body)
	}
	var doc struct {
		ID string `json:"id"`
	}
	json.Unmarshal([]byte(body), &doc)
	resp, body = tutorPost(t, srv, "/tutor/kb/documents/upload", upload)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"deduped":true`) ||
		!strings.Contains(body, `"id":"`+doc.ID+`"`) {
		t.Fatalf("kb dedup: %d %s", resp.StatusCode, body)
	}

	// Confirm, parse, read the text back.
	resp, body = tutorPost(t, srv, "/tutor/kb/documents/"+doc.ID+"/confirm", nil)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"status":"confirmed"`) {
		t.Fatalf("kb confirm: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorPost(t, srv, "/tutor/kb/documents/"+doc.ID+"/parse", map[string]string{"mode": "full"})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"status":"parsed"`) {
		t.Fatalf("kb parse: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents/"+doc.ID+"/parsed")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "组件复用") || !strings.Contains(body, `"truncated":false`) {
		t.Fatalf("kb parsed: %d %s", resp.StatusCode, body)
	}

	// Raw serves inline with the markdown media type.
	resp, body = tutorGet(t, srv, "/tutor/kb/documents/"+doc.ID+"/raw")
	if resp.StatusCode != http.StatusOK || !strings.Contains(resp.Header.Get("Content-Type"), "text/markdown") {
		t.Fatalf("kb raw: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, "# 指南") {
		t.Fatalf("kb raw body: %s", body)
	}

	// A text doc lands parsed immediately.
	resp, body = tutorPost(t, srv, "/tutor/kb/documents/text", map[string]string{
		"title": "Rust 手册", "text": "所有权是 Rust 最独特的概念。",
	})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"status":"parsed"`) || !strings.Contains(body, `"has_original":false`) {
		t.Fatalf("kb text: %d %s", resp.StatusCode, body)
	}
	var textDoc struct {
		ID string `json:"id"`
	}
	json.Unmarshal([]byte(body), &textDoc)

	// List: title hit and content hit, kind filter.
	resp, body = tutorGet(t, srv, "/tutor/kb/documents?q="+url.QueryEscape("手册"))
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "Rust 手册") || strings.Contains(body, "指南.md") {
		t.Fatalf("kb list title: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents?q="+url.QueryEscape("组件复用"))
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "指南.md") {
		t.Fatalf("kb list content: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents?kind=text")
	if resp.StatusCode != http.StatusOK || strings.Contains(body, "指南.md") {
		t.Fatalf("kb list kind: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents/"+doc.ID)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("kb doc get: %d %s", resp.StatusCode, body)
	}
	// Rename over PUT; GET reflects it.
	req, _ := http.NewRequest(http.MethodPut, srv.URL+"/tutor/kb/documents/"+doc.ID,
		strings.NewReader(`{"title":"React 指南"}`))
	req.Header.Set("Content-Type", "application/json")
	resp2, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusOK {
		t.Fatalf("kb rename: %d", resp2.StatusCode)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents/"+doc.ID)
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, "React 指南") {
		t.Fatalf("kb rename readback: %d %s", resp.StatusCode, body)
	}

	// Global search now carries the kb_docs domain.
	resp, body = tutorGet(t, srv, "/tutor/search?q="+url.QueryEscape("所有权"))
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"kb_docs":{"total":1`) ||
		!strings.Contains(body, "Rust 手册") {
		t.Fatalf("search kb_docs: %d %s", resp.StatusCode, body)
	}

	// Chat: the fake echoes the first context doc as its citation.
	resp, body = tutorPost(t, srv, "/tutor/kb/chat", map[string]any{
		"question": "什么是所有权？", "document_ids": []string{textDoc.ID},
	})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"answer":"据文档回答。"`) ||
		!strings.Contains(body, `"citations":[{"doc_id":"`+textDoc.ID+`","title":"Rust 手册"}]`) ||
		!strings.Contains(body, `"context_docs":1`) {
		t.Fatalf("kb chat: %d %s", resp.StatusCode, body)
	}
	var chat struct {
		RecordID string `json:"record_id"`
	}
	json.Unmarshal([]byte(body), &chat)
	resp, body = tutorGet(t, srv, "/tutor/kb/chats?q=所有权")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"total":1`) ||
		!strings.Contains(body, chat.RecordID) {
		t.Fatalf("kb chats: %d %s", resp.StatusCode, body)
	}

	// Export: manifest plus both faces of the uploaded doc.
	resp, body = tutorGet(t, srv, "/tutor/kb/export")
	if resp.StatusCode != http.StatusOK || !strings.Contains(resp.Header.Get("Content-Type"), "application/zip") ||
		resp.Header.Get("X-Tutor-Kb-Docs") != "2" {
		t.Fatalf("kb export: %d %s", resp.StatusCode, body)
	}
	zr, err := zip.NewReader(strings.NewReader(body), int64(len(body)))
	if err != nil {
		t.Fatalf("kb zip parse: %v", err)
	}
	if !slices.ContainsFunc(zr.File, func(f *zip.File) bool { return f.Name == "index.md" }) ||
		!slices.ContainsFunc(zr.File, func(f *zip.File) bool { return strings.HasPrefix(f.Name, "originals/"+doc.ID) }) ||
		!slices.ContainsFunc(zr.File, func(f *zip.File) bool { return strings.HasPrefix(f.Name, "parsed/"+textDoc.ID) }) {
		t.Fatal("kb zip entries missing")
	}

	// One-click generation seeds a session with parsed materials.
	resp, body = tutorPost(t, srv, "/tutor/kb/generate", map[string]any{
		"document_ids": []string{textDoc.ID}, "title": "KB 方案",
	})
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"material_count":1`) {
		t.Fatalf("kb generate: %d %s", resp.StatusCode, body)
	}
	var gen struct {
		SessionID string `json:"session_id"`
	}
	json.Unmarshal([]byte(body), &gen)
	resp, body = tutorGet(t, srv, "/tutor/sessions/"+gen.SessionID+"/materials")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"status":"parsed"`) {
		t.Fatalf("kb generate materials: %d %s", resp.StatusCode, body)
	}

	// Unparsed documents refuse generation; deletions are terminal.
	resp, body = tutorPost(t, srv, "/tutor/kb/generate", map[string]any{"document_ids": []string{"no-such-doc"}})
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("kb generate missing: %d %s", resp.StatusCode, body)
	}
	req, _ = http.NewRequest(http.MethodDelete, srv.URL+"/tutor/kb/documents/"+doc.ID, nil)
	resp2, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNoContent {
		t.Fatalf("kb delete: %d", resp2.StatusCode)
	}
	resp, body = tutorGet(t, srv, "/tutor/kb/documents/"+doc.ID)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("kb get after delete: %d %s", resp.StatusCode, body)
	}

	// Chat records delete with the same anti-enumeration refusal.
	req, _ = http.NewRequest(http.MethodDelete, srv.URL+"/tutor/kb/chats/"+chat.RecordID, nil)
	resp2, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNoContent {
		t.Fatalf("kb chat delete: %d", resp2.StatusCode)
	}
	req, _ = http.NewRequest(http.MethodDelete, srv.URL+"/tutor/kb/chats/"+chat.RecordID, nil)
	resp2, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotFound {
		t.Fatalf("kb chat re-delete: %d", resp2.StatusCode)
	}
}

func TestTutorOccupancyRefusals(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	_, body := tutorPost(t, srv, "/tutor/sessions", nil)
	var session struct {
		ID string `json:"id"`
	}
	json.Unmarshal([]byte(body), &session)

	// Clarify without a pause and retry without a failure both refuse 409.
	resp, _ := tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/clarify", map[string]any{"answers": []string{"一小时"}})
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("clarify refused with %d", resp.StatusCode)
	}
	resp, _ = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/retry", nil)
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("retry refused with %d", resp.StatusCode)
	}
	resp, _ = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/cancel", nil)
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("cancel refused with %d", resp.StatusCode)
	}
	// Unknown scope and plan refuse with the mapped codes.
	resp, _ = tutorPost(t, srv, "/tutor/plans/none/regenerate", map[string]string{"scope": "design_stages"})
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown plan regenerate refused with %d", resp.StatusCode)
	}
	// Generate without raw text refuses before the stream opens.
	resp, _ = tutorPost(t, srv, "/tutor/sessions/"+session.ID+"/generate", map[string]string{"raw_text": ""})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("empty generate refused with %d", resp.StatusCode)
	}
}

// The tutor's web search resolves through Reasonix's unified selection —
// agent.web_search_model in Model preferences — never a tutor-private
// engine or credential. The serve binary links the provider kinds it
// serves, so this side asserts the selection path without constructing a
// provider; the construction itself is boot's, tested beside its code.
func TestTutorSearcherUsesReasonixWebSearchSelection(t *testing.T) {
	home := t.TempDir()
	body := `
[agent]
web_search_model = "ghost/none"
`
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REASONIX_HOME", home)
	searcher, ok := tutorSearcherFromConfig().(*tutor.WebSearcher)
	if !ok {
		t.Fatalf("searcher is %T", searcher)
	}
	// A selection whose provider is gone answers Reasonix's own reason.
	_, ref, err := searcher.SearchModel()
	if err == nil || ref != "" {
		t.Fatalf("broken selection = %q, %v", ref, err)
	}
	if reason, ok := config.WebSearchReasonOf(err); !ok || reason != config.WebSearchModelRemoved {
		t.Fatalf("reason = %q, %v", reason, err)
	}
	// Without a selection the searcher answers empty and says so.
	t.Setenv("REASONIX_HOME", t.TempDir())
	p, ref, err := searcher.SearchModel()
	if err != nil || p != nil || ref != "" {
		t.Fatalf("unset selection = %v, %q, %v", p, ref, err)
	}
}
