package tutor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
)

// service_materials.go generates the self-study guide: unit splitting,
// per-unit content with screened supplements, the citation whitelist, and
// Deep-tutor's SSE vocabulary (unit_start / unit_done / done / error).

// MaterialsRequest is what the materials endpoint accepts.
type MaterialsRequest struct {
	Refresh      bool
	StageIndexes []int
	NodeIDs      []string
	UnitIDs      []string
	Depth        string // brief | full
}

// MaterialsEvent is one SSE frame of the generation stream.
type MaterialsEvent struct {
	Type string
	Data map[string]any
}

// MaterialsSnapshot is the synchronous answer: the whole rendered guide.
type MaterialsSnapshot struct {
	UnitCount int         `json:"unit_count"`
	Markdown  string      `json:"markdown"`
	IndexMD   string      `json:"index_md"`
	Units     []StudyUnit `json:"units,omitempty"`
}

// UnitSearcher supplements a unit with screened web resources; WebSearcher
// implements it and keeps the gate mandatory.
type UnitSearcher interface {
	SearchUnit(ctx context.Context, query string, limit int) []Resource
}

// GenerateMaterials is idempotent like Deep-tutor's endpoint: persisted
// units render back without touching a model unless the request names a
// subset to regenerate or forces refresh. Events stream through emit as each
// unit lands.
func (s *Service) GenerateMaterials(ctx context.Context, planID string, req MaterialsRequest, emit func(MaterialsEvent)) (*MaterialsSnapshot, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return nil, err
	}
	existing, _ := s.store.StudyUnits(planID)
	scoped := req.Refresh || len(req.StageIndexes) > 0 || len(req.NodeIDs) > 0 || len(req.UnitIDs) > 0
	if len(existing) > 0 && !scoped {
		return s.snapshot(plan, existing)
	}
	if s.budgetExceeded() {
		return nil, ErrBudgetExceeded
	}

	targets, err := resolveTargets(plan, existing, req)
	if err != nil {
		return nil, err
	}

	// The replacement set is the existing list with the targets re-rendered
	// in place; a full refresh starts from scratch.
	units := existing
	if req.Refresh || len(existing) == 0 {
		units = targets
	} else {
		for i := range units {
			for _, repl := range targets {
				if repl.Order == units[i].Order {
					units[i] = repl
				}
			}
		}
	}

	degraded := 0
	kept := 0
	for i := range units {
		unit := &units[i]
		target := containsOrder(targets, unit.Order)
		if !target {
			continue // untouched unit: already persisted content
		}
		if s.budgetExceeded() {
			if emit != nil {
				emit(MaterialsEvent{Type: "error", Data: map[string]any{
					"code": "budget_exceeded", "message": "本次生成预算已用尽，已保留已生成单元",
				}})
			}
			break
		}
		if emit != nil {
			emit(MaterialsEvent{Type: "unit_start", Data: map[string]any{
				"order": unit.Order + 1, "title": unit.Title, "total": len(units),
			}})
		}
		spec, wasDegraded, cost := s.generateUnit(ctx, plan, unit, len(units))
		unit.Objectives = spec.Objectives
		unit.Prerequisites = spec.Prerequisites
		unit.EstimatedTime = spec.EstimatedTime
		unit.ContentMD = spec.ContentMD
		unit.Degraded = wasDegraded
		if wasDegraded {
			degraded++
		}
		kept++
		if cost > 0 {
			_ = s.store.AddUsage(s.opt.now().Format("2006-01-02"), cost)
		}
		if emit != nil {
			emit(MaterialsEvent{Type: "unit_done", Data: map[string]any{
				"order": unit.Order + 1, "title": unit.Title, "degraded": wasDegraded,
			}})
		}
	}

	if kept == 0 && len(targets) > 0 && len(units) == 0 {
		return nil, errors.New("tutor: no units were generated")
	}
	if err := s.store.SaveStudyUnits(planID, units); err != nil {
		return nil, err
	}
	specs := make([]UnitSpec, len(units))
	for i, u := range units {
		specs[i] = UnitSpec{
			Title: u.Title, Objectives: u.Objectives, Prerequisites: u.Prerequisites,
			EstimatedTime: u.EstimatedTime, ContentMD: u.ContentMD,
		}
	}
	index := BuildMaterialsIndex(plan, specs)
	if err := s.store.WriteMaterialsIndex(planID, index); err != nil {
		return nil, err
	}
	if emit != nil {
		emit(MaterialsEvent{Type: "done", Data: map[string]any{
			"unit_count": len(units), "degraded_count": degraded,
		}})
	}
	return s.snapshot(plan, units)
}

func containsOrder(units []StudyUnit, order int) bool {
	for _, u := range units {
		if u.Order == order {
			return true
		}
	}
	return false
}

func (s *Service) snapshot(plan *Plan, units []StudyUnit) (*MaterialsSnapshot, error) {
	specs := make([]UnitSpec, len(units))
	for i, u := range units {
		specs[i] = UnitSpec{
			Title: u.Title, Objectives: u.Objectives, Prerequisites: u.Prerequisites,
			EstimatedTime: u.EstimatedTime, ContentMD: u.ContentMD,
		}
	}
	index, _ := s.store.ReadMaterialsIndex(plan.ID)
	if index == "" {
		index = BuildMaterialsIndex(plan, specs)
	}
	title := plan.Title
	if title == "" {
		title = plan.Topic
	}
	return &MaterialsSnapshot{
		UnitCount: len(units), Markdown: RenderMaterialsMD(title, specs),
		IndexMD: index, Units: units,
	}, nil
}

// resolveTargets builds the unit shells a request regenerates.
func resolveTargets(plan *Plan, existing []StudyUnit, req MaterialsRequest) ([]StudyUnit, error) {
	if len(req.UnitIDs) > 0 {
		if len(req.StageIndexes) > 0 || len(req.NodeIDs) > 0 {
			return nil, fmt.Errorf("%w: unit_ids 与 scope 互斥", ErrBadScope)
		}
		byID := map[string]StudyUnit{}
		for _, u := range existing {
			byID[u.ID] = u
		}
		var picked []StudyUnit
		for _, id := range req.UnitIDs {
			u, ok := byID[id]
			if !ok {
				return nil, fmt.Errorf("%w: 学习单元 %s", ErrNotFound, id)
			}
			picked = append(picked, u)
		}
		return picked, nil
	}
	plans := GroupNodesIntoUnits(plan.Knowledge.Nodes, plan.Stages)
	if len(plans) == 0 {
		plans = StageUnitPlans(plan.Stages)
	}
	var targets []StudyUnit
	for i, up := range plans {
		if !unitInScope(up, req) {
			continue
		}
		targets = append(targets, StudyUnit{
			ID: newID(), Order: i, Title: up.Title, StageIndex: up.StageIndex,
			NodeSIDs: nodeIDs(up.Nodes),
		})
	}
	if len(targets) == 0 {
		if len(req.StageIndexes) > 0 || len(req.NodeIDs) > 0 {
			return nil, fmt.Errorf("%w: scope 匹配不到任何单元", ErrBadScope)
		}
		return nil, errors.New("tutor: plan has no units to build")
	}
	return targets, nil
}

func unitInScope(up UnitPlan, req MaterialsRequest) bool {
	if len(req.StageIndexes) == 0 && len(req.NodeIDs) == 0 {
		return true
	}
	if slices.Contains(req.StageIndexes, up.StageIndex) {
		return true
	}
	wanted := map[string]bool{}
	for _, id := range req.NodeIDs {
		wanted[id] = true
	}
	for _, n := range up.Nodes {
		if wanted[n.SID] {
			return true
		}
	}
	return false
}

func nodeIDs(nodes []KnowledgeNode) []string {
	var ids []string
	for _, n := range nodes {
		if n.SID != "" {
			ids = append(ids, n.SID)
		}
	}
	return ids
}

// generateUnit renders one unit: screened web supplements, one LLM turn, the
// citation whitelist. Failure degrades to the brief outline, never to an
// empty unit.
func (s *Service) generateUnit(ctx context.Context, plan *Plan, unit *StudyUnit, total int) (spec UnitSpec, degraded bool, cost float64) {
	up := UnitPlan{Title: unit.Title, StageIndex: unit.StageIndex}
	stage := stageByIndex(plan, unit.StageIndex)
	var supplements []Resource
	if searcher, ok := s.opt.Searcher.(UnitSearcher); ok && s.opt.Provider != nil {
		if llm, err := s.opt.Provider(); err == nil && llm != nil {
			query := unit.Title
			if stage != nil && len(stage.Topics) > 0 {
				query = stage.Topics[0].Name
			}
			supplements = searcher.SearchUnit(ctx, query, 3)
		}
	}
	pool := CollectMaterialURLs(plan, supplements)

	var unitNodes []KnowledgeNode
	bySID := map[string]KnowledgeNode{}
	for _, n := range plan.Knowledge.Nodes {
		bySID[n.SID] = n
	}
	for _, id := range unit.NodeSIDs {
		if n, ok := bySID[id]; ok {
			unitNodes = append(unitNodes, n)
		}
	}
	user, err := unitUserPayload(plan, unit, stage, unitNodes, supplements, total)
	if err != nil {
		return BriefUnit(plan, up), true, 0
	}
	spec, usage, degradedReason, err := s.runUnitTool(ctx, unitPrompt, user)
	if s.opt.Pricer != nil {
		cost = s.opt.Pricer(usage.Model, usage)
	}
	if err != nil || strings.TrimSpace(spec.ContentMD) == "" {
		// No usable model text: the brief outline is the unit's floor.
		return BriefUnit(plan, up), true, cost
	}
	spec.Title = unit.Title
	spec.ContentMD = SanitizeCitations(spec.ContentMD, pool)
	return spec, degradedReason != "", cost
}

// runUnitTool drives the unit spec through the shared repair loop.
func (s *Service) runUnitTool(ctx context.Context, system, user string) (UnitSpec, Usage, string, error) {
	llm, err := s.opt.Provider()
	if err != nil || llm == nil {
		return UnitSpec{}, Usage{}, "no_llm_provider", ErrNoLLM
	}
	type unitReply struct {
		Objectives    []string `json:"objectives"`
		Prerequisites []string `json:"prerequisites"`
		EstimatedTime string   `json:"estimated_time"`
		ContentMD     string   `json:"content_md"`
	}
	tool := jsonTool[unitReply]{
		System: system,
		User:   func() string { return user },
		Decode: func(raw json.RawMessage) (unitReply, error) {
			var v unitReply
			if err := json.Unmarshal(raw, &v); err != nil {
				return v, fmt.Errorf("tutor: decode: %w", err)
			}
			if strings.TrimSpace(v.ContentMD) == "" {
				return v, errors.New("content_md 不能为空")
			}
			return v, nil
		},
		Default: func() unitReply { return unitReply{} },
	}
	reply, usage, degraded, err := runJSONTool(ctx, llm, s.opt.MaxTokens, tool)
	return UnitSpec{
		Objectives: reply.Objectives, Prerequisites: reply.Prerequisites,
		EstimatedTime: reply.EstimatedTime, ContentMD: reply.ContentMD,
	}, usage, degraded, err
}

// unitUserPayload assembles the model's input: a plan slice, this unit's
// nodes and stage, its supplements, and its position.
func unitUserPayload(plan *Plan, unit *StudyUnit, stage *Stage, nodes []KnowledgeNode, supplements []Resource, total int) (string, error) {
	type nodeLite struct {
		Name       string `json:"name"`
		Difficulty int    `json:"difficulty"`
		MustLearn  bool   `json:"must_learn"`
	}
	var nodeRows []nodeLite
	for _, n := range nodes {
		nodeRows = append(nodeRows, nodeLite{Name: n.Name, Difficulty: n.Difficulty, MustLearn: n.MustLearn})
	}
	stageCopy := (*Stage)(nil)
	if stage != nil {
		stageCopy = stage
	}
	payload := map[string]any{
		"plan": map[string]any{
			"intent":    plan.Intent,
			"stages":    plan.Stages,
			"resources": stageResources(plan),
		},
		"unit_nodes":  nodeRows,
		"stage":       stageCopy,
		"supplements": supplements,
		"order":       unit.Order + 1,
		"total":       total,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	return string(body) + "\n\n仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）。", nil
}

// stageResources flattens the plan's verified resources for the prompt.
func stageResources(plan *Plan) []map[string]string {
	var rows []map[string]string
	for _, st := range plan.Stages {
		for _, r := range st.Resources {
			rows = append(rows, map[string]string{
				"title": r.Title, "url": r.URL, "note": r.Note,
			})
		}
	}
	return rows
}
