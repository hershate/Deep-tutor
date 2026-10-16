package tutor

import (
	"sort"
	"strconv"
	"strings"
)

// studymaterials.go is the self-study guide: knowledge nodes cluster into
// 30-60 minute units, each unit's content is one focused LLM turn with a
// brief-outline fallback.

const (
	unitTargetMinutes = 55
	unitMaxNodes      = 3
	unitFallbackMin   = 3
	moduleSize        = 5
)

// nodeMinutes approximates a node's self-study weight by difficulty.
func nodeMinutes(difficulty int) int {
	switch {
	case difficulty <= 1:
		return 15
	case difficulty == 2:
		return 25
	case difficulty == 3:
		return 40
	default:
		return 55 + 5*(difficulty-4)
	}
}

// UnitPlan is one unit's shape before content generation.
type UnitPlan struct {
	Title      string
	StageIndex int
	HasStage   bool
	Nodes      []KnowledgeNode
}

// GroupNodesIntoUnits clusters nodes by (stage, difficulty): hard nodes
// (difficulty ≥ 4) stand alone, easier ones accumulate toward the minute
// target or the node cap. Fewer than unitFallbackMin nodes hand control
// back to stage-based units.
func GroupNodesIntoUnits(nodes []KnowledgeNode, stages []Stage) []UnitPlan {
	if len(nodes) < unitFallbackMin {
		return nil
	}
	stageIndex := func(n KnowledgeNode) int {
		for _, st := range stages {
			for _, t := range st.Topics {
				if t.Name == n.Name {
					return st.Index
				}
			}
		}
		bestPos := 0
		if len(stages) > 0 {
			for i := range stages {
				if abs(stages[i].Difficulty-n.Difficulty) < abs(stages[bestPos].Difficulty-n.Difficulty) {
					bestPos = i
				}
			}
			return stages[bestPos].Index
		}
		return 0
	}
	ordered := append([]KnowledgeNode(nil), nodes...)
	sort.SliceStable(ordered, func(i, j int) bool {
		si, sj := stageIndex(ordered[i]), stageIndex(ordered[j])
		if si != sj {
			return si < sj
		}
		return ordered[i].Difficulty < ordered[j].Difficulty
	})
	var plans []UnitPlan
	var buf []KnowledgeNode
	bufMinutes := 0
	flush := func() {
		if len(buf) == 0 {
			return
		}
		var names []string
		for _, n := range buf {
			if n.Name != "" {
				names = append(names, n.Name)
			}
		}
		title := "综合"
		if len(names) > 0 {
			title = strings.Join(names, "、")
			if r := []rune(title); len(r) > 30 {
				title = string(r[:29]) + "等"
			}
		}
		plans = append(plans, UnitPlan{
			Title: title, StageIndex: stageIndex(buf[0]), HasStage: true,
			Nodes: append([]KnowledgeNode(nil), buf...),
		})
		buf, bufMinutes = nil, 0
	}
	for _, n := range ordered {
		w := nodeMinutes(n.Difficulty)
		if len(buf) > 0 && (n.Difficulty >= 4 || bufMinutes+w > unitTargetMinutes || len(buf) >= unitMaxNodes) {
			flush()
		}
		buf = append(buf, n)
		bufMinutes += w
	}
	flush()
	return plans
}

// StageUnitPlans falls back to one unit per stage.
func StageUnitPlans(stages []Stage) []UnitPlan {
	plans := make([]UnitPlan, 0, len(stages))
	for _, st := range stages {
		title := st.Name
		if title == "" {
			title = "阶段 " + strconv.Itoa(st.Index+1)
		}
		plans = append(plans, UnitPlan{Title: title, StageIndex: st.Index, HasStage: true})
	}
	return plans
}

// UnitSpec is one unit's content: what a study-materials document renders.
type UnitSpec struct {
	Title         string   `json:"title"`
	Objectives    []string `json:"objectives"`
	Prerequisites []string `json:"prerequisites"`
	EstimatedTime string   `json:"estimated_time"`
	ContentMD     string   `json:"content_md"`
}

// unitPrompt is Deep-tutor's generate_unit_content inline prompt; the model
// sees the plan slice plus this unit's nodes and order.
const unitPrompt = `你是 Reasonix Tutor 的「自学单元内容生成」工具。任务：为学习方案中的一个阶段生成单个自学单元的内容（UnitContentSpec JSON）。非交互，直接输出规约。

输入（user message JSON）：{"plan":"学习方案切片（intent/stages/resources）","unit_nodes":"本单元要覆盖的知识点","stage":"本单元所属阶段","order":"第 n/N 个单元"}

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"objectives":["学完后能够..."],"prerequisites":["前置知识"],"estimated_time":"45 min","content_md":"markdown 内容"}

规则：
- 本单元对应 30-60 分钟自学量；2-4 个 objectives（以「学完后能够…」表述）；prerequisites 来自更早单元（第一单元可为 ["无"]）。
- content_md 为 markdown，用 ### 级小标题，依次包含：
  ### 概述（200-300 字：这是什么、为什么重要，日常语言）
  ### 核心概念（每概念：通俗解释（类比/日常场景）+ 要点 + 示例；覆盖 unit_nodes 的每个知识点；缺失时覆盖 stage.topics）
  ### 实践练习（基础练习 ≥2 个，由易到难；可附进阶挑战）
  ### 检查理解（2-4 个自测问题）
  ### 总结（150-200 字要点回顾）
  ### 扩展阅读（仅可引用输入 resources / supplements 中已有的 url，标注来源；无合适资源时写「无外部资料参考」）
- 禁止编造 URL：扩展阅读只可引用已给的 url，否则省略该节或标注「无外部资料参考」。
- 用自己的话组织内容，不粘贴资料原文；不编造方案中没有的知识点。
- 单元内容少时不强行填模板，保持简洁。

仅返回符合 schema 的 JSON 对象。`

// BriefUnit renders the deterministic outline a failed unit degrades to.
func BriefUnit(plan *Plan, unit UnitPlan) UnitSpec {
	stage := stageByIndex(plan, unit.StageIndex)
	var names []string
	for _, n := range unit.Nodes {
		if n.Name != "" {
			names = append(names, n.Name)
		}
	}
	lines := []string{"### 概述", ""}
	if len(names) > 0 {
		lines = append(lines, "本单元学习："+strings.Join(names, "、")+"。")
	} else {
		lines = append(lines, "本单元学习「"+unit.Title+"」。")
	}
	if stage != nil && stage.Goal != "" {
		lines = append(lines, "阶段目标："+stage.Goal+"。")
	}
	lines = append(lines, "")
	if len(names) > 0 {
		lines = append(lines, "### 核心概念", "")
		for _, name := range names {
			lines = append(lines, "- "+name)
		}
		lines = append(lines, "")
	}
	lines = append(lines, "（本单元内容生成失败，已降级为占位大纲，请刷新重试）")
	spec := UnitSpec{Title: unit.Title, ContentMD: strings.Join(lines, "\n")}
	if stage != nil {
		if stage.Goal != "" {
			spec.Objectives = []string{stage.Goal}
		}
		if stage.EstimatedWeeks > 0 {
			spec.EstimatedTime = strconv.FormatFloat(stage.EstimatedWeeks, 'f', -1, 64) + " 周"
		}
	}
	if spec.EstimatedTime == "" {
		spec.EstimatedTime = "30-60 min"
	}
	return spec
}

func stageByIndex(plan *Plan, index int) *Stage {
	for i := range plan.Stages {
		if plan.Stages[i].Index == index {
			return &plan.Stages[i]
		}
	}
	return nil
}

// RenderMaterialsMD writes the single study document; long unit lists break
// into modules so the reader sees progress, not a wall.
func RenderMaterialsMD(title string, units []UnitSpec) string {
	var lines []string
	if title == "" {
		title = "学习方案"
	}
	lines = append(lines, "# "+title, "", "> 共 "+strconv.Itoa(len(units))+" 个自学单元", "")
	modular := len(units) > 10
	for i, u := range units {
		if modular && i%moduleSize == 0 {
			end := min(i+moduleSize, len(units))
			lines = append(lines, "## 模块 "+strconv.Itoa(i/moduleSize+1)+
				"（单元 "+strconv.Itoa(i+1)+"-"+strconv.Itoa(end)+"）", "")
		}
		depth := "##"
		if modular {
			depth = "###"
		}
		lines = append(lines, depth+" "+strconv.Itoa(i+1)+". "+u.Title, "")
		var meta []string
		if u.EstimatedTime != "" {
			meta = append(meta, "**预计时长**："+u.EstimatedTime)
		}
		if len(u.Objectives) > 0 {
			meta = append(meta, "**学习目标**："+strings.Join(u.Objectives, "；"))
		}
		if len(u.Prerequisites) > 0 {
			meta = append(meta, "**前置知识**："+strings.Join(u.Prerequisites, "；"))
		}
		if len(meta) > 0 {
			lines = append(lines, strings.Join(meta, "  ·  "), "")
		}
		if u.ContentMD != "" {
			lines = append(lines, u.ContentMD, "")
		}
		lines = append(lines, "---", "")
	}
	return strings.Join(lines, "\n")
}

// BuildMaterialsIndex renders the roadmap: unit table, schedule, review
// checkpoints with must-learn nodes.
func BuildMaterialsIndex(plan *Plan, units []UnitSpec) string {
	title := plan.Title
	if title == "" {
		title = plan.Topic
	}
	if title == "" {
		title = "学习方案"
	}
	lines := []string{"# " + title + " · 自学路线图", "", "## 路线图", "",
		"| # | 单元 | 预计时长 | 前置 | 目标 |", "|---|---|---|---|---|"}
	for i, u := range units {
		prereq := "无"
		if len(u.Prerequisites) > 0 {
			prereq = strings.Join(u.Prerequisites, "、")
		}
		objectives := "—"
		if len(u.Objectives) > 0 {
			objectives = strings.Join(u.Objectives, "；")
		}
		est := u.EstimatedTime
		if est == "" {
			est = "—"
		}
		lines = append(lines, "| "+strconv.Itoa(i+1)+" | "+u.Title+" | "+est+" | "+prereq+" | "+objectives+" |")
	}
	lines = append(lines, "")
	if len(plan.Schedule) > 0 {
		lines = append(lines, "## 计划表", "", "| 日期 | 阶段 | 任务 | 预计分钟 |", "|---|---|---|---|")
		for _, d := range plan.Schedule {
			tasks := strings.Join(d.Tasks, "、")
			lines = append(lines, "| "+d.Date+" | "+strconv.Itoa(d.StageIndex)+" | "+tasks+" | "+
				strconv.Itoa(d.EstMinutes)+" |")
		}
		lines = append(lines, "")
	}
	lines = append(lines, "## 复习节点", "")
	for i, u := range units {
		lines = append(lines, "- [ ] 单元 "+strconv.Itoa(i+1)+"："+u.Title)
	}
	var must []string
	for _, n := range plan.Knowledge.Nodes {
		if n.MustLearn && n.Name != "" {
			must = append(must, n.Name)
		}
	}
	if len(must) > 0 {
		lines = append(lines, "", "必学知识点："+strings.Join(must, "、"))
	}
	lines = append(lines, "")
	return strings.Join(lines, "\n")
}

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}
