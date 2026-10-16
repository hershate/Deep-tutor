package serve

import (
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"reasonix/internal/runtime/tutor"
)

// TestTutorSlidesExport walks both slide routes: the plan deck and the
// per-unit deck, each over the deterministic path (no provider in tests),
// plus the missing-resource refusals.
func TestTutorSlidesExport(t *testing.T) {
	srv, store := newShareTestServer(t)

	sess, err := store.CreateSession("slides-flow")
	if err != nil {
		t.Fatal(err)
	}
	plan := &tutor.Plan{
		ID: "plan1", SessionID: sess.ID, Version: 1,
		Title: "Go 并发", Topic: "Go",
		CreatedAt: time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC),
		Intent:    tutor.Intent{Topic: "Go", Goal: "并发编程", DailyMinutes: 25},
		Stages:    []tutor.Stage{{Index: 0, Name: "阶段一", Goal: "goroutine 基础", Milestone: "会写"}},
		Schedule:  []tutor.ScheduleDay{{Date: "2026-10-15", StageIndex: 0, Tasks: []string{"读并发一章"}, EstMinutes: 30}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	sess.PlanID = plan.ID
	if err := store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveStudyUnits(plan.ID, []tutor.StudyUnit{{
		ID: "u1", Order: 1, Title: "goroutine 入门",
		ContentMD: "### 概述\n轻量级线程\n\n- 由运行时调度",
	}}); err != nil {
		t.Fatal(err)
	}

	get := func(path string) (*http.Response, string) {
		t.Helper()
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp, string(body)
	}

	resp, body := get("/tutor/plans/plan1/export.html")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("plan slides status=%d body=%s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Content-Type"); got != "text/html; charset=utf-8" {
		t.Fatalf("content-type=%q", got)
	}
	if cd := resp.Header.Get("Content-Disposition"); !strings.Contains(cd, `filename="plan-plan1.slides.html"`) {
		t.Fatalf("disposition=%q", cd)
	}
	if resp.Header.Get("X-Tutor-Slides") != "3" { // title + stage + schedule
		t.Fatalf("slides header=%q want 3", resp.Header.Get("X-Tutor-Slides"))
	}
	if resp.Header.Get("X-Tutor-Slides-Degraded") != "" {
		t.Fatal("deterministic deck is the expected offline path, not degraded")
	}
	for _, want := range []string{
		"<!DOCTYPE html>", "<title>Go</title>", "并发编程", "阶段 1：阶段一",
		`id="slide-1" class="slide active"`, "navDots", "</html>",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("plan deck missing %q", want)
		}
	}

	resp, body = get("/tutor/plans/plan1/units/u1/export.html")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("unit slides status=%d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(resp.Header.Get("Content-Disposition"), "unit-u1.slides.html") {
		t.Fatalf("unit disposition=%q", resp.Header.Get("Content-Disposition"))
	}
	if resp.Header.Get("X-Tutor-Slides") != "2" { // title + 概述
		t.Fatalf("unit slides header=%q want 2", resp.Header.Get("X-Tutor-Slides"))
	}
	if !strings.Contains(body, "goroutine 入门") || !strings.Contains(body, "轻量级线程") {
		t.Fatalf("unit deck content missing:\n%s", body)
	}

	resp, body = get("/tutor/plans/missing/export.html")
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(body, "tutor.not_found") {
		t.Fatalf("missing plan: status=%d body=%s", resp.StatusCode, body)
	}
	resp, body = get("/tutor/plans/plan1/units/missing/export.html")
	if resp.StatusCode != http.StatusNotFound || !strings.Contains(body, "tutor.not_found") {
		t.Fatalf("missing unit: status=%d body=%s", resp.StatusCode, body)
	}

	// The pdf export rides Deep-tutor's degraded path for good: markdown
	// body, .md filename, degradation warning header.
	resp, body = get("/tutor/plans/plan1/export.pdf")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("pdf status=%d body=%s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Content-Type"); got != "text/markdown; charset=utf-8" {
		t.Fatalf("pdf content-type=%q", got)
	}
	if resp.Header.Get("X-Tutor-Export-Warning") == "" {
		t.Fatal("pdf export must carry the degradation warning")
	}
	if cd := resp.Header.Get("Content-Disposition"); !strings.Contains(cd, `filename="plan-plan1.md"`) {
		t.Fatalf("pdf degraded disposition=%q", cd)
	}
	if !strings.Contains(body, "Go 并发") {
		t.Fatal("pdf degraded body must carry the plan markdown")
	}
}
