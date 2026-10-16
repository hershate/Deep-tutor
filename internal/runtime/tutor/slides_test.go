package tutor

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRenderSlidesShell(t *testing.T) {
	spec := SlideSpec{
		Title:      "Go 并发 <讲解>",
		Theme:      map[string]string{"--bg": "#0a0e1a", "--accent": "#3b82f6", "--bogus": "red"},
		Transition: "trans-slide-left",
		Slides: []SlideContent{
			{Title: "封面", Layout: "center", Animation: "anim-fade-in"},
			{Title: "第二页", ContentHTML: "<p>内容</p>", Layout: "left", Animation: "anim-slide-up"},
		},
	}
	htmlText := RenderSlidesHTML(spec)
	for _, want := range []string{
		"<title>Go 并发 &lt;讲解&gt;</title>",
		`<div class="presentation trans-slide-left" id="presentation">`,
		`<section id="slide-1" class="slide active" data-slide="1" role="region" aria-label="Slide 1" aria-hidden="false">`,
		`<section id="slide-2" class="slide" data-slide="2" role="region" aria-label="Slide 2" aria-hidden="true">`,
		"<h1 class=\"anim-fade-in\">封面</h1>",
		"<h2 class=\"anim-slide-up\">第二页</h2>",
		"--bg: #0a0e1a;",
		"--accent: #3b82f6;",
		"50.00%",
		"id=\"slideTotal\">2<",
		"navDots", "slideProgress", "prefers-reduced-motion",
		"anim-typewriter", "anim-highlight", "trans-flip",
	} {
		if !strings.Contains(htmlText, want) {
			t.Fatalf("missing %q in shell", want)
		}
	}
	if strings.Contains(htmlText, "--bogus") {
		t.Fatal("unknown theme variables must not enter the shell")
	}
	if strings.Contains(htmlText, "__SLIDES__") || strings.Contains(htmlText, "__ROOT_CSS__") ||
		strings.Contains(htmlText, "__PRESENTATION_OPEN__") || strings.Contains(htmlText, "__PROGRESS_PERCENT__") {
		t.Fatal("unreplaced marker left in shell")
	}
}

func TestRenderSlidesEmptyDeck(t *testing.T) {
	htmlText := RenderSlidesHTML(SlideSpec{Title: "空", Slides: nil})
	if !strings.Contains(htmlText, `id="slide-1" class="slide active"`) || strings.Contains(htmlText, "slide-2") {
		t.Fatal("empty deck must render one title slide")
	}
	if !strings.Contains(htmlText, "100.00%") {
		t.Fatal("single-slide progress must be 100%")
	}
}

func TestRenderSlidesSanitizesMarkup(t *testing.T) {
	spec := SlideSpec{
		Title: "净化",
		Slides: []SlideContent{{
			Title: "x\" onclick=\"steal()",
			ContentHTML: `<p onclick="steal()">stay</p>` +
				`<script>alert(1)</script><style>body{}</style>` +
				`<iframe src="x"></iframe><img src="x" onerror="y">` +
				`<a href="java&#115;cript:alert(1)">link</a>` +
				`<a href="java` + "\t" + `script:alert(2)">tab</a>` +
				`<a href="https://ok.example">ok</a>`,
			Layout:    `left" onmouseover="alert(3)`,
			Animation: `anim-fade-in"><script>x</script>`,
		}},
	}
	htmlText := RenderSlidesHTML(spec)
	// The shell legitimately carries its own <script> nav block and
	// onclick handlers; the scrub contract covers the slide region alone.
	start := strings.Index(htmlText, "<body>")
	end := strings.Index(htmlText, "<!-- Navigation -->")
	if start < 0 || end <= start {
		t.Fatal("shell markers missing")
	}
	slideRegion := strings.ToLower(htmlText[start:end])
	// Attribute-shaped tokens: escaped title text may spell "onclick" as
	// plain text (onclick=&quot;...), which is inert element content.
	for _, banned := range []string{
		"<script", "<iframe", "<style", `onclick="`, `onclick='`,
		`onmouseover="`, `onerror="`, "javascript:", "vbscript:",
	} {
		if strings.Contains(slideRegion, banned) {
			t.Fatalf("banned %q survived sanitize", banned)
		}
	}
	if !strings.Contains(htmlText, `href="https://ok.example"`) {
		t.Fatal("safe URL must survive")
	}
	if !strings.Contains(htmlText, `href="#"`) {
		t.Fatal("dangerous scheme must collapse to #")
	}
	if !strings.Contains(htmlText, ">stay<") {
		t.Fatal("ordinary content must survive")
	}
	// A class-injecting layout collapses to class-safe characters with no
	// quote breakout: the wrapper class stays one quoted attribute.
	if !strings.Contains(htmlText, `<div class="content-leftonmouseoveralert3">`) {
		t.Fatalf("layout injection must sanitize to one class token:\n%s", htmlText)
	}
}

func TestDefaultSpecFromPlan(t *testing.T) {
	plan := &Plan{
		ID: "p1", Title: "Rust 所有权", Topic: "Rust",
		Intent: Intent{Topic: "Rust", Level: "beginner", Goal: "掌握所有权", DailyMinutes: 30},
		Stages: []Stage{
			{Index: 0, Name: "基础", Goal: "理解移动语义", Milestone: "能解释 move",
				Topics:    []StageTopic{{Name: "move"}, {Name: "borrow"}},
				Resources: []Resource{{Title: "Rust 手册", URL: "https://doc.rust-lang.org/", Verified: VerifyOK}}},
			{Index: 1, Name: "进阶", Goal: "生命周期", Milestone: "能标注生命周期"},
		},
		Schedule:   []ScheduleDay{{Date: "2026-10-15", StageIndex: 0, Tasks: []string{"读第 4 章"}, EstMinutes: 30}},
		Validation: &ValidationReport{Passed: true},
	}
	spec := DefaultSpecFromPlan(plan)
	if spec.Title != "Rust" || spec.Theme["--bg"] != "#0f172a" || spec.Transition != "trans-fade" {
		t.Fatalf("spec header wrong: %+v", spec)
	}
	// title + 2 stages + resources + schedule + validation = 6 slides.
	if len(spec.Slides) != 6 {
		t.Fatalf("slides=%d want 6", len(spec.Slides))
	}
	if spec.Slides[0].Layout != "center" || !strings.Contains(spec.Slides[0].ContentHTML, "目标：掌握所有权") {
		t.Fatalf("title slide wrong: %+v", spec.Slides[0])
	}
	if spec.Slides[1].Title != "阶段 1：基础" || !strings.Contains(spec.Slides[1].ContentHTML, "<li>move</li>") ||
		!strings.Contains(spec.Slides[1].ContentHTML, "里程碑：能解释 move") {
		t.Fatalf("stage slide wrong: %+v", spec.Slides[1])
	}
	if spec.Slides[3].Title != "配套资源" || !strings.Contains(spec.Slides[3].ContentHTML, "Rust 手册") {
		t.Fatalf("resources slide wrong: %+v", spec.Slides[3])
	}
	if spec.Slides[4].Title != "时间安排" || spec.Slides[5].Title != "校验报告" {
		t.Fatalf("schedule/validation slides wrong")
	}
}

func TestDefaultSpecFromUnit(t *testing.T) {
	md := "### 概述\n开场白\n\n- 要点一 **加粗**\n- 要点二 `代码`\n\n```text\n### 围栏内不是标题\n```\n\n### 核心概念\n<p>所有</p>权"
	spec := DefaultSpecFromUnit("单元 A", md)
	// title + 概述 + 核心概念 = 3; the fenced ### must not split.
	if len(spec.Slides) != 3 {
		t.Fatalf("slides=%d want 3", len(spec.Slides))
	}
	overview := spec.Slides[1]
	if overview.Title != "概述" {
		t.Fatalf("section title=%q", overview.Title)
	}
	if !strings.Contains(overview.ContentHTML, "<p>开场白</p>") ||
		!strings.Contains(overview.ContentHTML, "<li>要点一 <strong>加粗</strong></li>") ||
		!strings.Contains(overview.ContentHTML, "<li>要点二 <code>代码</code></li>") {
		t.Fatalf("overview body wrong: %s", overview.ContentHTML)
	}
	if !strings.Contains(spec.Slides[2].ContentHTML, "&lt;p&gt;所有&lt;/p&gt;权") {
		t.Fatalf("raw HTML must be escaped: %s", spec.Slides[2].ContentHTML)
	}
	// The fenced ### stays inside the 概述 section's body, never splits a slide.
	if !strings.Contains(spec.Slides[1].ContentHTML, "### 围栏内不是标题") {
		t.Fatalf("fenced heading must stay in its section: %s", spec.Slides[1].ContentHTML)
	}
}

func TestDefaultSpecFromUnitNoSections(t *testing.T) {
	spec := DefaultSpecFromUnit("", "只有一段正文\n第二行")
	if len(spec.Slides) != 2 {
		t.Fatalf("slides=%d want 2 (title + whole body)", len(spec.Slides))
	}
	if spec.Title != "自学单元" {
		t.Fatalf("title=%q", spec.Title)
	}
	if !strings.Contains(spec.Slides[1].ContentHTML, "<p>只有一段正文</p>") {
		t.Fatalf("body slide wrong: %s", spec.Slides[1].ContentHTML)
	}
}

func newSlidesTestService(t *testing.T, reply func(system string, call int) string) (*Service, *Store) {
	t.Helper()
	store, err := OpenStore(filepath.Join(t.TempDir(), "tutor"))
	if err != nil {
		t.Fatal(err)
	}
	opts := ServiceOptions{}
	if reply != nil {
		opts.Provider = func() (LLM, error) { return &fakeLLM{t: t, reply: reply}, nil }
	}
	return NewService(store, opts), store
}

func slidesTestPlan() *Plan {
	return &Plan{
		ID: "pl1", Version: 1, Title: "Go 并发", Topic: "Go",
		Intent:   Intent{Topic: "Go", Goal: "并发编程", DailyMinutes: 25},
		Stages:   []Stage{{Index: 0, Name: "基础", Goal: "goroutine", Milestone: "会写"}},
		Schedule: []ScheduleDay{{Date: "2026-10-16", StageIndex: 0, Tasks: []string{"读"}, EstMinutes: 25}},
	}
}

func TestGeneratePlanSlidesDefaultDeck(t *testing.T) {
	svc, store := newSlidesTestService(t, nil) // no provider: deterministic deck
	if err := store.SavePlan(slidesTestPlan()); err != nil {
		t.Fatal(err)
	}
	res, err := svc.GeneratePlanSlides(context.Background(), "pl1")
	if err != nil {
		t.Fatalf("GeneratePlanSlides: %v", err)
	}
	if res.Degraded {
		t.Fatal("no provider is the expected offline path, not a degradation")
	}
	// title + one stage + schedule (no resources, no validation) = 3 slides.
	if res.SlideCount != 3 {
		t.Fatalf("slideCount=%d want 3", res.SlideCount)
	}
	if !strings.Contains(res.HTML, "并发编程") {
		t.Fatal("deck must carry plan content")
	}
}

func TestGeneratePlanSlidesLLMSpec(t *testing.T) {
	reply := `{"title":"LLM deck","theme":{"--bg":"#0a0e1a"},"transition":"trans-zoom","slides":[` +
		`{"title":"页一","content_html":"<p>甲</p>","layout":"center","animation":"anim-fade-in"},` +
		`{"title":"页二","content_html":"<p>乙</p>","layout":"grid-2","animation":"anim-bounce-in"}]}`
	svc, store := newSlidesTestService(t, func(_ string, _ int) string { return reply })
	if err := store.SavePlan(slidesTestPlan()); err != nil {
		t.Fatal(err)
	}
	res, err := svc.GeneratePlanSlides(context.Background(), "pl1")
	if err != nil {
		t.Fatal(err)
	}
	if res.Degraded {
		t.Fatal("valid LLM spec must not be flagged degraded")
	}
	if res.SlideCount != 2 || !strings.Contains(res.HTML, "LLM deck") ||
		!strings.Contains(res.HTML, "trans-zoom") || !strings.Contains(res.HTML, "--bg: #0a0e1a;") {
		t.Fatalf("LLM spec not honored: count=%d", res.SlideCount)
	}
}

func TestGeneratePlanSlidesLLMDegradedToDefault(t *testing.T) {
	call := 0
	svc, store := newSlidesTestService(t, func(_ string, _ int) string {
		call++
		return "not json at all"
	})
	if err := store.SavePlan(slidesTestPlan()); err != nil {
		t.Fatal(err)
	}
	res, err := svc.GeneratePlanSlides(context.Background(), "pl1")
	if err != nil {
		t.Fatal(err)
	}
	if !res.Degraded {
		t.Fatal("schema exhaustion with a provider present must flag degraded")
	}
	if call != repairAttempts+1 {
		t.Fatalf("repair loop calls=%d want %d", call, repairAttempts+1)
	}
	if res.SlideCount == 0 || !strings.Contains(res.HTML, "Go") {
		t.Fatal("fallback deck must render")
	}
}

func TestGeneratePlanSlidesMissingPlan(t *testing.T) {
	svc, _ := newSlidesTestService(t, nil)
	if _, err := svc.GeneratePlanSlides(context.Background(), "nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("err=%v want ErrNotFound", err)
	}
}

func TestGenerateUnitSlides(t *testing.T) {
	svc, store := newSlidesTestService(t, nil)
	if err := store.SavePlan(slidesTestPlan()); err != nil {
		t.Fatal(err)
	}
	units := []StudyUnit{{
		ID: "u1", Order: 1, Title: "goroutine 入门",
		ContentMD: "### 概述\n轻量级线程\n\n- 由运行时调度",
	}}
	if err := store.SaveStudyUnits("pl1", units); err != nil {
		t.Fatal(err)
	}
	res, err := svc.GenerateUnitSlides(context.Background(), "pl1", "u1")
	if err != nil {
		t.Fatalf("GenerateUnitSlides: %v", err)
	}
	if res.Degraded || res.SlideCount != 2 {
		t.Fatalf("unit deck wrong: count=%d degraded=%v", res.SlideCount, res.Degraded)
	}
	if !strings.Contains(res.HTML, "goroutine 入门") || !strings.Contains(res.HTML, "轻量级线程") {
		t.Fatal("unit content missing from deck")
	}
	if _, err := svc.GenerateUnitSlides(context.Background(), "pl1", "missing"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("err=%v want ErrNotFound", err)
	}
}

func TestSlidesSpecChargesUsage(t *testing.T) {
	reply := `{"title":"d","slides":[{"title":"a","content_html":"<p>x</p>","layout":"left","animation":"anim-fade-in"}]}`
	svc, store := newSlidesTestService(t, func(_ string, _ int) string { return reply })
	if err := store.SavePlan(slidesTestPlan()); err != nil {
		t.Fatal(err)
	}
	svc.opt.Pricer = func(model string, u Usage) float64 {
		if u.InputTokens == 0 {
			t.Fatal("usage must carry the LLM's tokens")
		}
		return 0.5
	}
	if _, err := svc.GeneratePlanSlides(context.Background(), "pl1"); err != nil {
		t.Fatal(err)
	}
	day, err := store.UsageOfDay(time.Now().Format("2006-01-02"))
	if err != nil {
		t.Fatal(err)
	}
	if day.Cost < 0.5 {
		t.Fatalf("ledger cost=%v want the priced 0.5", day.Cost)
	}
}
