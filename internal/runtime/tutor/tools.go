package tutor

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Pipeline tool names, in canonical order. The order is the single source
// for step numbering, regeneration windows, and the parallel groups below.
const (
	ToolParseMaterial     = "parse_material"
	ToolUnderstandIntent  = "understand_intent"
	ToolBuildKnowledgeMap = "build_knowledge_map"
	ToolDesignStages      = "design_stages"
	ToolSearchResources   = "search_resources"
	ToolSchedulePlan      = "schedule_plan"
	ToolValidatePlan      = "validate_plan"
	ToolExportPlan        = "export_plan"
)

var pipeline = []string{
	ToolParseMaterial, ToolUnderstandIntent, ToolBuildKnowledgeMap,
	ToolDesignStages, ToolSearchResources, ToolSchedulePlan,
	ToolValidatePlan, ToolExportPlan,
}

// parallelGroups splits the pipeline into execution groups; search_resources
// and schedule_plan run concurrently.
var parallelGroups = [][]string{
	{ToolParseMaterial},
	{ToolUnderstandIntent},
	{ToolBuildKnowledgeMap},
	{ToolDesignStages},
	{ToolSearchResources, ToolSchedulePlan},
	{ToolValidatePlan},
	{ToolExportPlan},
}

// feedbackTargets maps a failing validation check to the tool that must rerun.
var feedbackTargets = map[string]string{
	"dependency_closure":    ToolBuildKnowledgeMap,
	"difficulty_step":       ToolDesignStages,
	"cognitive_load":        ToolDesignStages,
	"milestone_feasibility": ToolDesignStages,
	"gap_analysis":          ToolDesignStages,
}

const maxFeedbackRounds = 2

type CallStatus string

const (
	CallOK       CallStatus = "ok"
	CallDegraded CallStatus = "degraded"
	CallError    CallStatus = "error"
)

// ToolOutput is the pipeline's uniform envelope: status, decoded data,
// degradation reason, error identity, and metered metadata.
type ToolOutput struct {
	Status   CallStatus `json:"status"`
	Degraded string     `json:"degraded,omitempty"`
	Data     any        `json:"data,omitempty"`
	Error    string     `json:"error,omitempty"`
	Meta     toolMeta   `json:"meta"`
}

func okOutput(data any, meta toolMeta) ToolOutput {
	return ToolOutput{Status: CallOK, Data: data, Meta: meta}
}

func degradedOutput(data any, reason string, meta toolMeta) ToolOutput {
	return ToolOutput{Status: CallDegraded, Degraded: reason, Data: data, Meta: meta}
}

func errorOutput(tool string, err error, meta toolMeta) ToolOutput {
	return ToolOutput{Status: CallError, Error: errIdentity(err), Meta: meta}
}

// errIdentity renders an error for the transcript without losing its class.
func errIdentity(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// ResourcesResult is search_resources' output.
type ResourcesResult struct {
	Resources []Resource     `json:"resources"`
	ByStage   map[string]int `json:"by_stage,omitempty"`
	Note      string         `json:"note,omitempty"`
	Engine    string         `json:"engine,omitempty"`
}

// ValidatePlanData is validate_plan's decoded output.
type ValidatePlanData struct {
	Report ValidationReport `json:"report"`
	Passed bool             `json:"passed"`
	Fixes  []string         `json:"fixes,omitempty"`
}

// Searcher supplies candidate learning resources. The real implementation
// drives web search with verification and the screening gate; a nil Searcher
// yields an honest empty result.
type Searcher interface {
	Search(ctx context.Context, q ResourceQuery) (SearchOutcome, error)
}

// ResourceQuery names what resources must fit.
type ResourceQuery struct {
	Topic  string
	Goal   string
	Level  string
	Stages []Stage
	Nodes  int
}

func degradedValidate() ValidatePlanData {
	return ValidatePlanData{
		Report: ValidationReport{
			DependencyClosure:    CheckResult{Status: "warn", Details: "校验工具降级，未执行"},
			DifficultyStep:       CheckResult{Status: "warn", Details: "校验工具降级，未执行"},
			CognitiveLoad:        CheckResult{Status: "warn", Details: "校验工具降级，未执行"},
			MilestoneFeasibility: CheckResult{Status: "warn", Details: "校验工具降级，未执行"},
			GapAnalysis:          CheckResult{Status: "warn", Details: "校验工具降级，未执行"},
			Passed:               true,
			Degraded:             true,
		},
		Passed: true,
		Fixes:  []string{"校验工具降级，已跳过自动校验，请人工复核"},
	}
}

// execParseMaterial merges the session's parsed materials into one digest.
// Per-material parsing happens at confirm time; this step only aggregates.
func (r *Runner) execParseMaterial(ctx context.Context, in RunInput) ToolOutput {
	start := time.Now()
	out, _ := r.Store.materialsParsedOutput(in.Session.ID)
	out.Meta.DurationMs += time.Since(start).Milliseconds()
	return out
}

// materialsParsedOutput merges the session's parsed materials into the
// parse_material output shape, shared by the pipeline step and by run starts
// that inject it as initial upstream instead of executing the tool.
func (s *Store) materialsParsedOutput(sessionID string) (ToolOutput, bool) {
	materials, err := s.Materials(sessionID)
	if err != nil {
		return errorOutput(ToolParseMaterial, err, toolMeta{}), false
	}
	var texts []string
	total := 0
	for _, m := range materials {
		if m.Status != "parsed" {
			continue
		}
		text, err := s.ReadMaterialParsed(m.SessionID, m.ID)
		if err != nil {
			continue
		}
		texts = append(texts, text)
		total += len([]rune(text))
	}
	if len(texts) == 0 {
		// No parsed materials: the aggregate is an empty success, matching the
		// Python tool's behavior on an empty table.
		return okOutput(chunked("", "materials", "merge"), toolMeta{}), false
	}
	merged := strings.Join(texts, "\n\n---\n\n")
	parsed := chunked(merged, "materials", "merge")
	parsed.Meta.Chars = total
	return okOutput(parsed, toolMeta{}), true
}

func (r *Runner) execUnderstandIntent(ctx context.Context, in RunInput, attempt int) ToolOutput {
	start := time.Now()
	system, err := SkillPrompt("intent")
	if err != nil {
		return errorOutput(ToolUnderstandIntent, err, toolMeta{DurationMs: time.Since(start).Milliseconds()})
	}
	materialsDigest := r.materialsDigest(in)

	tool := jsonTool[Intent]{
		System: system,
		User: func() string {
			var sb strings.Builder
			sb.WriteString("用户原始描述：\n" + in.RawText + "\n\n")
			if materialsDigest != "" {
				sb.WriteString("已解析资料（截断）：\n" + truncateForPrompt(materialsDigest) + "\n\n")
			}
			if len(in.Answers) > 0 {
				sb.WriteString("用户对澄清问题的回答：\n" + strings.Join(in.Answers, "\n") + "\n\n")
			}
			if in.TimeFields.DailyMinutes != 0 || in.TimeFields.Deadline != "" || in.TimeFields.StartDate != "" {
				sb.WriteString("用户表单显式指定（权威输入，直接采用）：\n")
				if in.TimeFields.DailyMinutes != 0 {
					fmt.Fprintf(&sb, "daily_minutes=%d\n", in.TimeFields.DailyMinutes)
				}
				if in.TimeFields.Deadline != "" {
					sb.WriteString("deadline=" + in.TimeFields.Deadline + "\n")
				}
				if in.TimeFields.StartDate != "" {
					sb.WriteString("start_date=" + in.TimeFields.StartDate + "\n")
				}
				sb.WriteString("\n")
			}
			sb.WriteString(`仅返回符合 schema 的 JSON 对象（不要 markdown 代码块、不要解释）。`)
			return sb.String()
		},
		Decode:  decodeStrict[Intent],
		Default: DegradedIntent,
	}
	value, usage, degraded, err := runJSONTool(ctx, r.LLM, r.MaxTokens, tool)
	meta := metaFrom(usage, r.Pricer, start)
	if err != nil {
		return errorOutput(ToolUnderstandIntent, err, meta)
	}
	// The clarify-continuation guard reads Answers, which ride RunInput's own
	// field, not TimeFields. Without the copy, an answered round whose
	// extraction still says needs_clarify would re-ask the same question.
	args := in.TimeFields
	args.Answers = in.Answers
	ValidateIntent(&value, args, time.Now())
	if degraded != "" {
		return degradedOutput(value, degraded, meta)
	}
	return okOutput(value, meta)
}

func (r *Runner) execKnowledgeMap(ctx context.Context, in RunInput, outputs map[string]ToolOutput, fixes []string) ToolOutput {
	start := time.Now()
	system, err := SkillPrompt("knowledge-map")
	if err != nil {
		return errorOutput(ToolBuildKnowledgeMap, err, toolMeta{DurationMs: time.Since(start).Milliseconds()})
	}
	intent := intentOf(outputs)
	materialsDigest := r.materialsDigest(in)

	tool := jsonTool[KnowledgeMap]{
		System: system,
		User: func() string {
			var sb strings.Builder
			sb.WriteString("学习意图：\n" + mustJSON(intent) + "\n\n")
			if materialsDigest != "" {
				sb.WriteString("已解析资料（截断）：\n" + truncateForPrompt(materialsDigest) + "\n\n")
			}
			if len(fixes) > 0 {
				sb.WriteString("校验反馈（必须修复，重点是依赖关系的完整与顺序）：\n" + strings.Join(fixes, "\n") + "\n\n")
			}
			sb.WriteString(`依据意图与资料拆解知识点依赖图谱。仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）。`)
			return sb.String()
		},
		Decode:  decodeStrict[KnowledgeMap],
		Default: func() KnowledgeMap { return KnowledgeMap{} },
	}
	value, usage, degraded, err := runJSONTool(ctx, r.LLM, r.MaxTokens, tool)
	meta := metaFrom(usage, r.Pricer, start)
	if err != nil {
		return errorOutput(ToolBuildKnowledgeMap, err, meta)
	}
	CleanKnowledgeMap(&value)
	if degraded != "" {
		return degradedOutput(value, degraded, meta)
	}
	return okOutput(value, meta)
}

type stagesData struct {
	Stages []Stage `json:"stages"`
}

func (r *Runner) execDesignStages(ctx context.Context, in RunInput, outputs map[string]ToolOutput, fixes []string, attempt int) ToolOutput {
	start := time.Now()
	system, err := SkillPrompt("stages")
	if err != nil {
		return errorOutput(ToolDesignStages, err, toolMeta{DurationMs: time.Since(start).Milliseconds()})
	}
	intent := intentOf(outputs)
	kmap := knowledgeOf(outputs)

	tool := jsonTool[stagesData]{
		System: system,
		User: func() string {
			var sb strings.Builder
			sb.WriteString("学习意图：\n" + mustJSON(intent) + "\n\n")
			sb.WriteString("知识图谱：\n" + mustJSON(kmap) + "\n\n")
			if len(fixes) > 0 {
				sb.WriteString("校验反馈（必须修复）：\n" + strings.Join(fixes, "\n") + "\n\n")
			}
			sb.WriteString(`设计 3-6 个渐进难度学习阶段。仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）。`)
			return sb.String()
		},
		Decode: decodeStrict[stagesData],
		Verified: func(v stagesData) error {
			return ValidateStages(v.Stages)
		},
		Default: func() stagesData { return stagesData{Stages: DefaultStages()} },
	}
	value, usage, degraded, err := runJSONTool(ctx, r.LLM, r.MaxTokens, tool)
	meta := metaFrom(usage, r.Pricer, start)
	if err != nil {
		return errorOutput(ToolDesignStages, err, meta)
	}
	NormalizeStages(value.Stages)
	if degraded != "" {
		return degradedOutput(value, degraded, meta)
	}
	return okOutput(value, meta)
}

const schedulePrompt = `你是学习排期工具。任务：依据学习阶段、每日可用时长与截止日期，生成逐日学习计划。

要求：
- schedule：逐日安排，每条含 date(ISO 8601)、stage_index、tasks（当日任务列表）、est_minutes。
- 每日 est_minutes 不超过每日可用时长；阶段难度高的日子可少排。
- 覆盖所有阶段；阶段顺序与设计一致。
- 若时长不足以覆盖所有阶段，仍生成一个合理的排期（后续会校验并提示）。
- 日期从指定起始日开始连续排列；未提供则从今天开始。不要自行编造起始日。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS 基础"],"est_minutes":60}]}`

func (r *Runner) execSchedulePlan(ctx context.Context, in RunInput, outputs map[string]ToolOutput) ToolOutput {
	start := time.Now()
	intent := intentOf(outputs)
	stages := stagesOf(outputs)

	tool := jsonTool[ScheduleData]{
		System: schedulePrompt,
		User: func() string {
			var sb strings.Builder
			summary := stagesSummary(stages)
			sb.WriteString("学习阶段：\n" + summary + "\n\n")
			fmt.Fprintf(&sb, "每日可用分钟数：%d\n", intentDailyMinutes(intent))
			if intent.StartDate != "" {
				sb.WriteString("起始日期：" + intent.StartDate + "\n")
			}
			if intent.Deadline != "" {
				sb.WriteString("截止日期：" + intent.Deadline + "\n")
			}
			sb.WriteString("\n仅返回符合 schema 的 JSON 对象。")
			return sb.String()
		},
		Decode:  decodeStrict[ScheduleData],
		Default: func() ScheduleData { return ScheduleData{} },
	}
	value, usage, degraded, err := runJSONTool(ctx, r.LLM, r.MaxTokens, tool)
	meta := metaFrom(usage, r.Pricer, start)
	if err != nil {
		return errorOutput(ToolSchedulePlan, err, meta)
	}
	CheckSchedule(&value, intentDailyMinutes(intent), intent.StartDate, time.Now())
	if degraded != "" {
		return degradedOutput(value, degraded, meta)
	}
	return okOutput(value, meta)
}

func (r *Runner) execSearchResources(ctx context.Context, in RunInput, outputs map[string]ToolOutput) ToolOutput {
	start := time.Now()
	if r.Searcher == nil {
		meta := toolMeta{DurationMs: time.Since(start).Milliseconds()}
		return degradedOutput(ResourcesResult{Note: "搜索未配置，本次未检索网络资源"}, "search_unavailable", meta)
	}
	intent := intentOf(outputs)
	stages := stagesOf(outputs)
	kmap := knowledgeOf(outputs)
	outcome, err := r.Searcher.Search(ctx, ResourceQuery{
		Topic: intent.Topic, Goal: intent.Goal, Level: string(intent.Level),
		Stages: stages, Nodes: len(kmap.Nodes),
	})
	meta := metaFrom(outcome.Usage, r.Pricer, start)
	// The web-search model spends under its own rate card; fold that into
	// the same budget the screening gate's spend feeds.
	if u := outcome.SearchUsage; u.InputTokens+u.OutputTokens > 0 {
		meta.Tokens += u.InputTokens + u.OutputTokens
		if r.Pricer != nil {
			meta.Cost += r.Pricer(u.Model, u)
		}
	}
	if err != nil {
		return degradedOutput(ResourcesResult{Note: "检索失败：" + err.Error()}, "search_failed", meta)
	}
	if outcome.Note != "" && len(outcome.Resources) == 0 {
		return degradedOutput(ResourcesResult{Note: outcome.Note, Engine: outcome.Engine}, "search_degraded", meta)
	}
	byStage := map[string]int{}
	for _, res := range outcome.Resources {
		byStage[fmt.Sprintf("%d", res.StageIndex)]++
	}
	return okOutput(ResourcesResult{Resources: outcome.Resources, ByStage: byStage, Engine: outcome.Engine}, meta)
}

func (r *Runner) execValidatePlan(ctx context.Context, in RunInput, outputs map[string]ToolOutput) ToolOutput {
	start := time.Now()
	system, err := SkillPrompt("validate")
	if err != nil {
		return errorOutput(ToolValidatePlan, err, toolMeta{DurationMs: time.Since(start).Milliseconds()})
	}
	plan := AssemblePlan(in.Session, outputs)

	tool := jsonTool[ValidatePlanData]{
		System: system,
		User: func() string {
			return "待校验学习方案：\n" + mustJSON(map[string]any{
				"intent":   plan.Intent,
				"stages":   plan.Stages,
				"schedule": plan.Schedule,
			}) + "\n\n仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）。"
		},
		Decode:  decodeStrict[ValidatePlanData],
		Default: degradedValidate,
	}
	value, usage, degraded, err := runJSONTool(ctx, r.LLM, r.MaxTokens, tool)
	meta := metaFrom(usage, r.Pricer, start)
	if err != nil {
		return errorOutput(ToolValidatePlan, err, meta)
	}
	if degraded != "" {
		return degradedOutput(value, degraded, meta)
	}
	return okOutput(value, meta)
}

// ExportResult is export_plan's output: the rendered markdown plus format.
type ExportResult struct {
	Format   string `json:"format"`
	Markdown string `json:"markdown"`
}

func (r *Runner) execExportPlan(ctx context.Context, in RunInput, outputs map[string]ToolOutput) ToolOutput {
	start := time.Now()
	plan := AssemblePlan(in.Session, outputs)
	md := RenderPlanMarkdown(plan)
	return okOutput(ExportResult{Format: "md", Markdown: md},
		toolMeta{DurationMs: time.Since(start).Milliseconds()})
}

func (r *Runner) materialsDigest(in RunInput) string {
	out, ok := in.InitialUpstream[ToolParseMaterial]
	if !ok || out.Status != CallOK {
		return ""
	}
	parsed, ok := out.Data.(*ParsedMaterial)
	if !ok {
		return ""
	}
	return parsed.Text
}

func intentOf(outputs map[string]ToolOutput) Intent {
	out, ok := outputs[ToolUnderstandIntent]
	if !ok || out.Status == CallError {
		return Intent{}
	}
	switch v := out.Data.(type) {
	case Intent:
		return v
	case *Intent:
		if v != nil {
			return *v
		}
	}
	return Intent{}
}

func knowledgeOf(outputs map[string]ToolOutput) KnowledgeMap {
	out, ok := outputs[ToolBuildKnowledgeMap]
	if !ok || out.Status == CallError {
		return KnowledgeMap{}
	}
	switch v := out.Data.(type) {
	case KnowledgeMap:
		return v
	case *KnowledgeMap:
		if v != nil {
			return *v
		}
	}
	return KnowledgeMap{}
}

func stagesOf(outputs map[string]ToolOutput) []Stage {
	out, ok := outputs[ToolDesignStages]
	if !ok || out.Status == CallError {
		return nil
	}
	switch v := out.Data.(type) {
	case stagesData:
		return v.Stages
	case *stagesData:
		if v != nil {
			return v.Stages
		}
	}
	return nil
}

func intentDailyMinutes(intent Intent) int {
	if intent.DailyMinutes >= 1 && intent.DailyMinutes <= 1440 {
		return intent.DailyMinutes
	}
	return 0
}

func stagesSummary(stages []Stage) string {
	var sb strings.Builder
	for _, st := range stages {
		fmt.Fprintf(&sb, "- 阶段%d %s（难度%d，约%g周，里程碑：%s）\n", st.Index, st.Name, st.Difficulty, st.EstimatedWeeks, st.Milestone)
		for _, t := range st.Topics {
			marker := ""
			if t.MustLearn {
				marker = "（必修）"
			}
			fmt.Fprintf(&sb, "  - %s%s\n", t.Name, marker)
		}
	}
	return sb.String()
}

func mustJSON(v any) string {
	data, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(data)
}
