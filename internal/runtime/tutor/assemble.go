package tutor

import (
	"fmt"
	"strings"
	"time"
)

// AssemblePlan folds pipeline outputs into one plan value. It is total:
// missing sections assemble as zero values so partial plans can render at
// every pipeline step.
func AssemblePlan(sess *GenSession, outputs map[string]ToolOutput) *Plan {
	intent := intentOf(outputs)
	kmap := knowledgeOf(outputs)
	stages := append([]Stage(nil), stagesOf(outputs)...)

	if out, ok := outputs[ToolSearchResources]; ok && out.Status != CallError {
		if res, ok := out.Data.(ResourcesResult); ok {
			attachResources(stages, res.Resources)
		} else if res, ok := out.Data.(*ResourcesResult); ok && res != nil {
			attachResources(stages, res.Resources)
		}
	}

	plan := &Plan{
		SessionID: sess.ID,
		Title:     intent.Topic,
		Topic:     intent.Topic,
		Intent:    intent,
		Knowledge: kmap,
		Stages:    stages,
		CreatedAt: time.Now(),
	}
	if plan.Title == "" {
		plan.Title = sess.Title
	}
	if out, ok := outputs[ToolSchedulePlan]; ok && out.Status != CallError {
		switch v := out.Data.(type) {
		case ScheduleData:
			plan.Schedule = v.Schedule
		case *ScheduleData:
			if v != nil {
				plan.Schedule = v.Schedule
			}
		}
	}
	if out, ok := outputs[ToolValidatePlan]; ok && out.Status != CallError {
		switch v := out.Data.(type) {
		case ValidatePlanData:
			report := v.Report
			report.Passed = v.Passed
			report.Fixes = v.Fixes
			plan.Validation = &report
		case *ValidatePlanData:
			if v != nil {
				report := v.Report
				report.Passed = v.Passed
				report.Fixes = v.Fixes
				plan.Validation = &report
			}
		}
	}
	var meta GenMeta
	for _, tool := range pipeline {
		out, ok := outputs[tool]
		if !ok {
			continue
		}
		meta.TotalTokens += out.Meta.Tokens
		meta.Cost += out.Meta.Cost
		if out.Meta.Model != "" {
			meta.Model = out.Meta.Model
		}
	}
	plan.Meta = meta
	return plan
}

func attachResources(stages []Stage, resources []Resource) {
	for i := range stages {
		stages[i].Resources = nil
	}
	for _, res := range resources {
		idx := res.StageIndex
		if idx < 0 || idx >= len(stages) {
			continue
		}
		stages[idx].Resources = append(stages[idx].Resources, res)
	}
}

var verifyMark = map[VerifyLabel]string{
	VerifyOK:        "✅",
	VerifyUncertain: "⚠️",
	VerifyInvalid:   "❌",
}

// RenderPlanMarkdown is export_plan's deterministic renderer: every plan
// section in order, resources marked by verification label.
func RenderPlanMarkdown(p *Plan) string {
	var sb strings.Builder
	fmt.Fprintf(&sb, "# 学习方案：%s\n\n", p.Title)
	renderIntentMarkdown(&sb, &p.Intent)
	renderKnowledgeMarkdown(&sb, &p.Knowledge)
	if len(p.Stages) > 0 {
		sb.WriteString("\n## 学习阶段\n")
	}
	for _, st := range p.Stages {
		renderStageMarkdown(&sb, st)
	}
	renderScheduleMarkdown(&sb, p.Schedule)
	renderValidationMarkdown(&sb, p.Validation)
	if p.Meta.TotalTokens > 0 || p.Meta.Cost > 0 {
		fmt.Fprintf(&sb, "\n---\n生成用量：%d tokens，成本 %.4f\n", p.Meta.TotalTokens, p.Meta.Cost)
	}
	return sb.String()
}

func renderIntentMarkdown(sb *strings.Builder, in *Intent) {
	sb.WriteString("## 学习意图\n\n")
	fmt.Fprintf(sb, "- 主题：%s\n", in.Topic)
	if in.Level != "" {
		fmt.Fprintf(sb, "- 当前基础：%s\n", in.Level)
	}
	if in.Goal != "" {
		fmt.Fprintf(sb, "- 学习目标：%s\n", in.Goal)
	}
	if in.DailyMinutes > 0 {
		fmt.Fprintf(sb, "- 每日可投入：%d 分钟\n", in.DailyMinutes)
	}
	if in.Deadline != "" {
		fmt.Fprintf(sb, "- 截止日期：%s\n", in.Deadline)
	}
	if in.StartDate != "" {
		fmt.Fprintf(sb, "- 起始日期：%s\n", in.StartDate)
	}
}

func renderKnowledgeMarkdown(sb *strings.Builder, kmap *KnowledgeMap) {
	if len(kmap.Nodes) == 0 {
		return
	}
	sb.WriteString("\n## 知识图谱\n\n")
	for _, n := range kmap.Nodes {
		marker := ""
		if n.MustLearn {
			marker = "（必修）"
		}
		parent := ""
		if n.ParentID != "" {
			parent = " ← " + n.ParentID
		}
		fmt.Fprintf(sb, "- %s %s%s，难度 %d%s\n", n.SID, n.Name, marker, n.Difficulty, parent)
	}
	if len(kmap.Edges) > 0 {
		sb.WriteString("\n依赖关系：\n")
		for _, e := range kmap.Edges {
			line := fmt.Sprintf("- %s → %s", e.From, e.To)
			if e.Relation != "" {
				line += "（" + e.Relation + "）"
			}
			sb.WriteString(line + "\n")
		}
	}
	if kmap.Note != "" {
		fmt.Fprintf(sb, "\n> %s\n", kmap.Note)
	}
}

func renderStageMarkdown(sb *strings.Builder, st Stage) {
	fmt.Fprintf(sb, "\n### 阶段 %d：%s（难度 %d，约 %g 周）\n\n", st.Index, st.Name, st.Difficulty, st.EstimatedWeeks)
	if st.Goal != "" {
		fmt.Fprintf(sb, "目标：%s\n\n", st.Goal)
	}
	if len(st.Topics) > 0 {
		sb.WriteString("知识点：\n")
		for _, t := range st.Topics {
			marker := ""
			if t.MustLearn {
				marker = "（必修）"
			}
			fmt.Fprintf(sb, "- %s%s", t.Name, marker)
			if t.Why != "" {
				fmt.Fprintf(sb, " — %s", t.Why)
			}
			sb.WriteString("\n")
		}
		sb.WriteString("\n")
	}
	if len(st.Resources) > 0 {
		sb.WriteString("资源：\n")
		for _, res := range st.Resources {
			fmt.Fprintf(sb, "- %s [%s] %s — %s\n", verifyMark[res.Verified], res.Type, res.Title, res.URL)
			if res.Note != "" {
				fmt.Fprintf(sb, "  - %s\n", res.Note)
			}
		}
		sb.WriteString("\n")
	}
	if len(st.Exercises) > 0 {
		sb.WriteString("练习：\n")
		for _, ex := range st.Exercises {
			fmt.Fprintf(sb, "- %s（约 %g 小时）\n", ex.Desc, ex.EstHours)
		}
		sb.WriteString("\n")
	}
	if st.Milestone != "" {
		fmt.Fprintf(sb, "里程碑：%s\n", st.Milestone)
	}
}

func renderScheduleMarkdown(sb *strings.Builder, schedule []ScheduleDay) {
	if len(schedule) == 0 {
		return
	}
	sb.WriteString("\n## 学习日程\n\n")
	sb.WriteString("| 日期 | 阶段 | 任务 | 预计分钟 |\n|---|---|---|---|\n")
	for _, day := range schedule {
		fmt.Fprintf(sb, "| %s | %d | %s | %d |\n", day.Date, day.StageIndex, strings.Join(day.Tasks, "；"), day.EstMinutes)
	}
}

func renderValidationMarkdown(sb *strings.Builder, v *ValidationReport) {
	if v == nil {
		return
	}
	sb.WriteString("\n## 校验报告\n\n")
	checks := []struct {
		name   string
		result CheckResult
	}{
		{"依赖闭包", v.DependencyClosure},
		{"难度阶梯", v.DifficultyStep},
		{"认知负荷", v.CognitiveLoad},
		{"里程碑可行性", v.MilestoneFeasibility},
		{"新手盲点", v.GapAnalysis},
	}
	for _, c := range checks {
		status := c.result.Status
		if status == "" {
			status = "warn"
		}
		fmt.Fprintf(sb, "- %s：**%s**", c.name, status)
		if c.result.Details != "" {
			fmt.Fprintf(sb, " — %s", c.result.Details)
		}
		sb.WriteString("\n")
	}
	if v.Degraded {
		sb.WriteString("\n> 校验工具降级，本报告未经自动校验，请人工复核。\n")
	}
	if len(v.Fixes) > 0 {
		sb.WriteString("\n修复建议：\n")
		for _, f := range v.Fixes {
			fmt.Fprintf(sb, "- %s\n", f)
		}
	}
}
