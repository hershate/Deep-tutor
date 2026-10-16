package tutor

// slides.go renders a plan (or one self-study unit) as a self-contained
// HTML slide deck, Deep-tutor's build_plan_slides ported natively: an LLM
// spec step with a deterministic default deck on every failure path.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"maps"
	"regexp"
	"strings"
)

// SlideContent is one slide. ContentHTML is embeddable markup — LLM output
// passes the render-time scrub; the default builders emit safe HTML.
type SlideContent struct {
	Title       string `json:"title"`
	ContentHTML string `json:"content_html"`
	Layout      string `json:"layout"`    // center | left | grid-2 | grid-3
	Animation   string `json:"animation"` // anim-* class
}

// SlideSpec is the deck specification: the LLM's output schema and the
// renderer's input.
type SlideSpec struct {
	Title      string            `json:"title"`
	Theme      map[string]string `json:"theme,omitempty"` // CSS variable -> value
	Transition string            `json:"transition"`      // trans-* class
	Slides     []SlideContent    `json:"slides"`
}

// slidesDefaultTheme overrides the shell's light defaults with the deck's
// dark presentation look; unlisted variables keep the shell's own values.
var slidesDefaultTheme = map[string]string{
	"--bg":             "#0f172a",
	"--bg-alt":         "#1e293b",
	"--text":           "#e2e8f0",
	"--text-secondary": "#94a3b8",
	"--accent":         "#3b82f6",
	"--accent-hover":   "#2563eb",
	"--border":         "#334155",
	"--font-display":   "'Georgia', serif",
	"--font-body":      "system-ui, sans-serif",
}

func slideEsc(s string) string { return html.EscapeString(s) }

// DefaultSpecFromPlan builds the fallback deck: title slide, one slide per
// stage, then resources, schedule, and validation when present.
func DefaultSpecFromPlan(plan *Plan) SlideSpec {
	topic := plan.Intent.Topic
	if topic == "" {
		topic = "学习方案"
	}
	slides := []SlideContent{}

	var sub []string
	if plan.Intent.Level != "" {
		sub = append(sub, "基础："+slideEsc(plan.Intent.Level))
	}
	if plan.Intent.Goal != "" {
		sub = append(sub, "目标："+slideEsc(plan.Intent.Goal))
	}
	if plan.Intent.DailyMinutes > 0 {
		sub = append(sub, fmt.Sprintf("每日：%d 分钟", plan.Intent.DailyMinutes))
	}
	content := ""
	if len(sub) > 0 {
		content = `<p style="font-size:1.3em;">` + strings.Join(sub, "<br>") + "</p>"
	}
	slides = append(slides, SlideContent{Title: topic, ContentHTML: content, Layout: "center", Animation: "anim-fade-in"})

	for _, st := range plan.Stages {
		var b strings.Builder
		b.WriteString("<p>" + slideEsc(st.Goal) + "</p>")
		if len(st.Topics) > 0 {
			b.WriteString("<ul>")
			for _, tp := range st.Topics {
				b.WriteString("<li>" + slideEsc(tp.Name) + "</li>")
			}
			b.WriteString("</ul>")
		}
		if st.Milestone != "" {
			b.WriteString(`<p style="margin-top:1em;">里程碑：` + slideEsc(st.Milestone) + "</p>")
		}
		slides = append(slides, SlideContent{
			Title: fmt.Sprintf("阶段 %d：%s", st.Index+1, st.Name), ContentHTML: b.String(),
			Layout: "left", Animation: "anim-slide-up",
		})
	}

	var resourceTitles []string
	for _, st := range plan.Stages {
		for _, r := range st.Resources {
			resourceTitles = append(resourceTitles, r.Title)
			if len(resourceTitles) >= 20 {
				break
			}
		}
	}
	if len(resourceTitles) > 0 {
		var b strings.Builder
		b.WriteString("<ul>")
		for _, title := range resourceTitles {
			b.WriteString("<li>" + slideEsc(title) + "</li>")
		}
		b.WriteString("</ul>")
		slides = append(slides, SlideContent{Title: "配套资源", ContentHTML: b.String(), Layout: "left"})
	}

	if len(plan.Schedule) > 0 {
		var b strings.Builder
		b.WriteString("<ul>")
		for _, d := range plan.Schedule[:min(20, len(plan.Schedule))] {
			b.WriteString("<li>" + slideEsc(d.Date) + "：" + slideEsc(strings.Join(d.Tasks, "、")) + "</li>")
		}
		b.WriteString("</ul>")
		slides = append(slides, SlideContent{Title: "时间安排", ContentHTML: b.String(), Layout: "left"})
	}

	if plan.Validation != nil {
		passed := "未通过 ⚠️"
		if plan.Validation.Passed {
			passed = "通过 ✅"
		}
		slides = append(slides, SlideContent{Title: "校验报告", ContentHTML: "<p>总体：" + passed + "</p>", Layout: "center"})
	}

	return SlideSpec{Title: topic, Theme: copySlidesTheme(slidesDefaultTheme), Transition: "trans-fade", Slides: slides}
}

// copySlidesTheme clones the default theme so a spec's overrides never leak
// into later decks.
func copySlidesTheme(src map[string]string) map[string]string {
	dst := make(map[string]string, len(src))
	maps.Copy(dst, src)
	return dst
}

// DefaultSpecFromUnit builds the fallback deck for one self-study unit:
// title slide plus one slide per ### section (whole body as one slide when
// the markdown carries no ### headings).
func DefaultSpecFromUnit(rawTitle, contentMD string) SlideSpec {
	deckTitle := titleOr(rawTitle, "自学单元")
	slides := []SlideContent{{Title: deckTitle, Layout: "center", Animation: "anim-fade-in"}}
	for _, sec := range splitMDSections(contentMD) {
		slides = append(slides, SlideContent{
			Title: sec.heading, ContentHTML: mdBlockToHTML(sec.body),
			Layout: "left", Animation: "anim-slide-up",
		})
	}
	if len(slides) == 1 {
		if body := mdBlockToHTML(contentMD); body != "" {
			slides = append(slides, SlideContent{Title: titleOr(rawTitle, "内容"), ContentHTML: body, Layout: "left", Animation: "anim-slide-up"})
		}
	}
	return SlideSpec{Title: deckTitle, Theme: copySlidesTheme(slidesDefaultTheme), Transition: "trans-fade", Slides: slides}
}

func titleOr(title, fallback string) string {
	if strings.TrimSpace(title) != "" {
		return title
	}
	return fallback
}

type mdSection struct{ heading, body string }

var mdHeadingRe = regexp.MustCompile(`^###\s+(.+?)\s*#*\s*$`)

// splitMDSections splits markdown on ### headings, skipping # lines inside
// ``` fences.
func splitMDSections(md string) []mdSection {
	if md == "" {
		return nil
	}
	var out []mdSection
	inCode := false
	curHead := ""
	var curHeadSet bool
	var buf []string
	flush := func() {
		if curHeadSet {
			out = append(out, mdSection{curHead, strings.TrimSpace(strings.Join(buf, "\n"))})
		}
	}
	for line := range strings.SplitSeq(md, "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "```") {
			inCode = !inCode
			buf = append(buf, line)
			continue
		}
		if !inCode {
			if m := mdHeadingRe.FindStringSubmatch(line); m != nil {
				flush()
				curHead, curHeadSet = strings.TrimSpace(m[1]), true
				buf = nil
				continue
			}
		}
		buf = append(buf, line)
	}
	flush()
	return out
}

// inlineMD escapes then restores **bold** and `code`.
func inlineMD(s string) string {
	s = slideEsc(s)
	s = regexp.MustCompile(`\*\*(.+?)\*\*`).ReplaceAllString(s, "<strong>$1</strong>")
	return regexp.MustCompile("`(.+?)`").ReplaceAllString(s, "<code>$1</code>")
}

var mdListItemRe = regexp.MustCompile(`^\s*[-*+]\s+(.+)$`)

// mdBlockToHTML is the deterministic markdown-to-HTML fallback: consecutive
// list items become <ul>, other non-empty lines become <p>.
func mdBlockToHTML(body string) string {
	if strings.TrimSpace(body) == "" {
		return ""
	}
	var blocks []string
	var list []string
	flush := func() {
		if len(list) > 0 {
			items := make([]string, len(list))
			for i, x := range list {
				items[i] = "<li>" + inlineMD(x) + "</li>"
			}
			blocks = append(blocks, "<ul>"+strings.Join(items, "")+"</ul>")
			list = nil
		}
	}
	for raw := range strings.SplitSeq(body, "\n") {
		line := strings.TrimRight(raw, " \t\r")
		if strings.TrimSpace(line) == "" {
			flush()
			continue
		}
		if m := mdListItemRe.FindStringSubmatch(line); m != nil {
			list = append(list, strings.TrimSpace(m[1]))
			continue
		}
		flush()
		blocks = append(blocks, "<p>"+inlineMD(strings.TrimSpace(line))+"</p>")
	}
	flush()
	return strings.Join(blocks, "")
}

// The LLM spec step and its schema contract follow.

const (
	slidesMaxDeckSize    = 50
	slidesMaxContentHTML = 20000
)

// verifySlideSpec enforces the schema contract the prompts state: a title,
// at least one slide, bounded deck and content sizes.
func verifySlideSpec(spec *SlideSpec) error {
	if strings.TrimSpace(spec.Title) == "" {
		return errors.New("title 不能为空")
	}
	if len(spec.Slides) == 0 {
		return errors.New("slides 不能为空")
	}
	if len(spec.Slides) > slidesMaxDeckSize {
		return fmt.Errorf("slides 数 %d 超上限 %d", len(spec.Slides), slidesMaxDeckSize)
	}
	for i := range spec.Slides {
		if len(spec.Slides[i].ContentHTML) > slidesMaxContentHTML {
			return fmt.Errorf("第 %d 页 content_html 超长度上限", i+1)
		}
	}
	return nil
}

const planSlidesPrompt = `你是 Reasonix 导师的「幻灯片规约生成」工具。任务：从学习方案（user message 的 JSON）生成 PPT 风格幻灯片规约（SlideSpec JSON），供渲染为自包含 HTML 幻灯片。非交互，直接输出规约。

输入：user message 的 JSON 学习方案，含 intent{topic,level,goal,daily_minutes}、stages[{index,name,goal,topics[{name}],milestone,resources[{title,url}]}]、schedule[{date,tasks}]、validation_report。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"方案标题","theme":{"--bg":"#色值","--bg-alt":"#色值","--text":"#色值","--text-secondary":"#色值","--accent":"#色值","--accent-hover":"#色值","--border":"#色值","--font-display":"字体栈","--font-body":"字体栈"},"transition":"trans-fade|trans-slide-left|trans-slide-right|trans-slide-up|trans-zoom|trans-flip|trans-none","slides":[{"title":"页标题","content_html":"HTML 内容","layout":"center|left|grid-2|grid-3","animation":"anim-fade-in|anim-slide-up|anim-slide-down|anim-slide-left|anim-slide-right|anim-scale-in|anim-rotate-in|anim-blur-in|anim-flip-in|anim-bounce-in"}]}

规则：
- 第 1 页标题页：layout=center，title=intent.topic，content_html 含 level/goal/daily_minutes 副标题。
- 每个学习阶段一页：title=阶段名，content_html 含 goal、知识点 <ul>、里程碑。
- 资源页、排期页、校验页各一页（按需，内容从方案取）。
- 总页数 5-15。
- content_html 仅用 <p>/<ul>/<ol>/<li>/<strong>/<em>/<br>；禁 <script>/<style>/<iframe>/<img>。
- theme 选合适配色（如深色科技 #0a0e1a/#3b82f6 或浅色商务 #ffffff/#1a1a1a）。
- animation/transition 用上述类名之一。
- 不编造方案中没有的内容；资料未体现的字段省略。

仅返回符合 schema 的 JSON 对象。`

const unitSlidesPrompt = `你是 Reasonix 导师的「自学单元幻灯片规约生成」工具。任务：从单个自学单元的标题与 Markdown 正文（user message 的 JSON：title / content_md）生成 PPT 风格幻灯片规约（SlideSpec JSON），供渲染为自包含 HTML 幻灯片。非交互，直接输出规约。

content_md 通常含 ### 概述 / ### 核心概念 / ### 实践练习 / ### 检查理解 / ### 总结 / ### 扩展阅读 等小节。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"单元标题","theme":{"--bg":"#色值","--bg-alt":"#色值","--text":"#色值","--text-secondary":"#色值","--accent":"#色值","--accent-hover":"#色值","--border":"#色值","--font-display":"字体栈","--font-body":"字体栈"},"transition":"trans-fade|trans-slide-left|trans-slide-right|trans-slide-up|trans-zoom|trans-flip|trans-none","slides":[{"title":"页标题","content_html":"HTML 内容","layout":"center|left|grid-2|grid-3","animation":"anim-fade-in|anim-slide-up|anim-slide-down|anim-slide-left|anim-slide-right|anim-scale-in|anim-rotate-in|anim-blur-in|anim-flip-in|anim-bounce-in"}]}

规则：
- 第 1 页标题页：layout=center，title=单元标题。
- 按单元正文小节/逻辑切分为多页（通常 4-8 页），每页聚焦一个要点；勿把全部内容塞一页，也不要为凑页数拆得过碎。
- content_html 用 <p>/<ul>/<ol>/<li>/<strong>/<em>/<br> 把正文提炼为演示要点（精炼、可视化，勿整段照搬 markdown 长文）；禁 <script>/<style>/<iframe>/<img>。
- theme 选合适配色（深色科技 #0a0e1a/#3b82f6 或浅色商务 #ffffff/#1a1a1a）；animation/transition 用上述类名之一。
- 不编造 content_md 中没有的内容；保留关键示例与「检查理解」问题。
- 总页数 4-10。

仅返回符合 schema 的 JSON 对象。`

// slidesResult is one export outcome: the rendered deck plus what the HTTP
// layer needs for its warning header.
type slidesResult struct {
	HTML       string
	SlideCount int
	Degraded   bool
}

// slidesBudgetGuard refuses the LLM path once the day's ledger is full —
// the deterministic deck always answers.
func (s *Service) slidesBudgetGuard() error {
	if s.budgetExceeded() {
		return ErrBudgetExceeded
	}
	return nil
}

func (s *Service) slidesCharge(usage Usage) {
	if s.opt.Pricer == nil {
		return
	}
	if cost := s.opt.Pricer(usage.Model, usage); cost > 0 {
		_ = s.store.AddUsage(s.opt.now().Format("2006-01-02"), cost)
	}
}

// GeneratePlanSlides renders a plan as a self-contained slide deck.
func (s *Service) GeneratePlanSlides(ctx context.Context, planID string) (slidesResult, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return slidesResult{}, err
	}
	planBody, err := json.Marshal(map[string]any{
		"title": plan.Title, "topic": plan.Topic, "intent": plan.Intent,
		"stages": plan.Stages, "schedule": plan.Schedule,
		"validation_report": plan.Validation,
	})
	if err != nil {
		return slidesResult{}, err
	}
	spec, usage, why, err := s.slidesSpec(ctx, planSlidesPrompt, string(planBody), func() SlideSpec {
		return DefaultSpecFromPlan(plan)
	})
	if err != nil {
		return slidesResult{}, err
	}
	s.slidesCharge(usage)
	return s.renderSlides(spec, why), nil
}

// GenerateUnitSlides renders one persisted self-study unit the same way.
func (s *Service) GenerateUnitSlides(ctx context.Context, planID, unitID string) (slidesResult, error) {
	units, err := s.store.StudyUnits(planID)
	if err != nil {
		return slidesResult{}, err
	}
	var unit *StudyUnit
	for i := range units {
		if units[i].ID == unitID {
			unit = &units[i]
			break
		}
	}
	if unit == nil {
		return slidesResult{}, ErrNotFound
	}
	unitBody, err := json.Marshal(map[string]any{
		"title": titleOr(unit.Title, "自学单元"), "content_md": unit.ContentMD,
	})
	if err != nil {
		return slidesResult{}, err
	}
	spec, usage, why, err := s.slidesSpec(ctx, unitSlidesPrompt, string(unitBody), func() SlideSpec {
		return DefaultSpecFromUnit(unit.Title, unit.ContentMD)
	})
	if err != nil {
		return slidesResult{}, err
	}
	s.slidesCharge(usage)
	return s.renderSlides(spec, why), nil
}

// slidesSpec runs the LLM spec step with the repair loop; a missing
// provider or exhausted repairs land on the deterministic deck. Only a
// schema failure with a provider present counts as degraded. The budget
// guard sits after provider resolution: a key-less user's deterministic
// deck is free and must never be blocked by an exhausted day.
func (s *Service) slidesSpec(ctx context.Context, system, user string, fallback func() SlideSpec) (SlideSpec, Usage, string, error) {
	llm, err := s.opt.Provider()
	if err != nil || llm == nil {
		return fallback(), Usage{}, "", nil
	}
	if err := s.slidesBudgetGuard(); err != nil {
		return fallback(), Usage{}, "", err
	}
	spec, usage, why, err := runJSONTool(ctx, llm, s.opt.MaxTokens, jsonTool[SlideSpec]{
		System:   system,
		User:     func() string { return user },
		Decode:   decodeStrict[SlideSpec],
		Verified: func(v SlideSpec) error { return verifySlideSpec(&v) },
		Default:  fallback,
	})
	if err != nil {
		// Transport failure: the deck still renders from the deterministic
		// spec, with the LLM's metered usage charged.
		return fallback(), usage, "llm_failed", nil
	}
	return spec, usage, why, nil
}

// renderSlides closes the outcome: the deck renders regardless of which
// path produced the spec; degraded only flags an LLM failure.
func (s *Service) renderSlides(spec SlideSpec, why string) slidesResult {
	return slidesResult{
		HTML:       RenderSlidesHTML(spec),
		SlideCount: len(spec.Slides),
		Degraded:   why == "schema_violation" || why == "llm_failed",
	}
}
