package tutor

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"sort"
	"strings"
)

// QuizItem is one generated question in Deep-tutor's QuizSpec wire shape.
type QuizItem struct {
	NodeID      string   `json:"node_id"`
	NodeName    string   `json:"node_name"`
	Type        string   `json:"type"`
	Question    string   `json:"question"`
	Options     []string `json:"options,omitempty"`
	Answer      string   `json:"answer"`
	Explanation string   `json:"explanation,omitempty"`
}

type quizSpec struct {
	Title   string     `json:"title"`
	Quizzes []QuizItem `json:"quizzes"`
}

// QuizSet is the generate/GET response: the shared title plus every quiz
// with its payload merged, Deep-tutor's {**payload, id} shape.
type QuizSet struct {
	Title     string           `json:"title"`
	QuizCount int              `json:"quiz_count"`
	Quizzes   []map[string]any `json:"quizzes"`
}

func quizSet(title string, quizzes []Quiz) QuizSet {
	items := make([]map[string]any, 0, len(quizzes))
	for _, q := range quizzes {
		items = append(items, map[string]any{
			"id": q.ID, "node_id": q.NodeID, "type": string(q.Type),
			"question": q.Payload.Question, "options": q.Payload.Options,
			"answer": q.Payload.Answer, "explanation": q.Payload.Explanation,
		})
	}
	return QuizSet{Title: title, QuizCount: len(quizzes), Quizzes: items}
}

// GenerateQuizzes builds a plan's quiz set: the persisted set when it exists
// and refresh is unset, the LLM's via the quiz skill otherwise, and the
// deterministic self-assessed fallback when no provider or the model fails —
// never a fabricated answer.
func (s *Service) GenerateQuizzes(ctx context.Context, planID string, refresh bool) (QuizSet, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return QuizSet{}, err
	}
	if !refresh {
		if quizzes, err := s.store.Quizzes(planID); err == nil && len(quizzes) > 0 {
			return quizSet(quizTitle(plan), quizzes), nil
		}
	}
	spec := s.generateQuizSpec(ctx, plan)
	quizzes := make([]Quiz, 0, len(spec.Quizzes))
	now := s.opt.now()
	for _, item := range spec.Quizzes {
		quizzes = append(quizzes, Quiz{
			ID: newID(), PlanID: planID, NodeID: item.NodeID,
			Type: NormalizeQuizType(item.Type),
			Payload: QuizPayload{
				Question: item.Question, Options: item.Options,
				Answer: item.Answer, Explanation: item.Explanation,
			},
			CreatedAt: now,
		})
	}
	if err := s.store.SaveQuizzes(planID, quizzes); err != nil {
		return QuizSet{}, err
	}
	// Replace semantics: the old set's attempts went with it.
	_ = s.store.ClearAttempts(planID)
	return quizSet(spec.Title, quizzes), nil
}

func quizTitle(plan *Plan) string {
	if plan.Topic != "" {
		return plan.Topic + "测验"
	}
	return "学习方案测验"
}

func (s *Service) generateQuizSpec(ctx context.Context, plan *Plan) quizSpec {
	llm, err := s.opt.Provider()
	if err == nil {
		if spec, err := s.llmQuizSpec(ctx, llm, plan); err == nil {
			return spec
		}
	}
	return defaultQuizFromPlan(plan)
}

// llmQuizSpec asks the quiz skill for the question set and repairs a
// non-conforming answer, mirroring the pipeline's JSON tool loop.
func (s *Service) llmQuizSpec(ctx context.Context, llm LLM, plan *Plan) (quizSpec, error) {
	system, err := SkillPrompt("quiz")
	if err != nil {
		return quizSpec{}, err
	}
	args := map[string]any{
		"plan": map[string]any{
			"intent": map[string]any{"topic": plan.Intent.Topic, "level": plan.Intent.Level, "goal": plan.Intent.Goal},
			"knowledge_map": map[string]any{
				"nodes": knowledgeNodesBrief(plan),
			},
			"stages": stagesBrief(plan),
		},
	}
	tool := jsonTool[quizSpec]{
		System: system,
		User: func() string {
			return mustJSON(args) + "\n仅返回符合 schema 的 JSON 对象（不要 markdown 代码块、不要解释）。"
		},
		Decode:   decodeStrict[quizSpec],
		Verified: func(spec quizSpec) error { return verifyQuizSpec(&spec) },
		Default:  func() quizSpec { return defaultQuizFromPlan(plan) },
	}
	spec, _, degraded, err := runJSONTool(ctx, llm, s.opt.MaxTokens, tool)
	if err != nil {
		return quizSpec{}, err
	}
	if degraded != "" {
		// A degraded answer (schema violation, no provider) already is the
		// deterministic fallback; hand it through.
		return spec, nil
	}
	return spec, nil
}

// verifyQuizSpec states the first constraint a generated set breaks, in the
// skill's own terms; nil means conforming.
func verifyQuizSpec(spec *quizSpec) error {
	if n := len(spec.Quizzes); n < 5 || n > 20 {
		return fmt.Errorf("总题数须在 5-20 之间，当前 %d", n)
	}
	for _, q := range spec.Quizzes {
		if strings.TrimSpace(q.Question) == "" {
			return errors.New("题干不能为空")
		}
		switch NormalizeQuizType(q.Type) {
		case QuizChoice:
			if len(q.Options) < 3 || len(q.Options) > 4 {
				return errors.New("choice 题选项须为 3-4 个")
			}
			if q.Answer == "" {
				return errors.New("choice 题必须提供正确选项原文")
			}
			if !slices.Contains(q.Options, q.Answer) {
				return errors.New("choice 题的 answer 必须是 options 中某一项的原文")
			}
		}
	}
	return nil
}

func knowledgeNodesBrief(plan *Plan) []map[string]any {
	nodes := make([]map[string]any, 0, len(plan.Knowledge.Nodes))
	for _, n := range plan.Knowledge.Nodes {
		nodes = append(nodes, map[string]any{
			"id": n.SID, "name": n.Name, "difficulty": n.Difficulty, "must_learn": n.MustLearn,
		})
	}
	return nodes
}

func stagesBrief(plan *Plan) []map[string]any {
	stages := make([]map[string]any, 0, len(plan.Stages))
	for _, st := range plan.Stages {
		topics := make([]string, 0, len(st.Topics))
		for _, t := range st.Topics {
			topics = append(topics, t.Name)
		}
		stages = append(stages, map[string]any{"name": st.Name, "topics": topics})
	}
	return stages
}

// defaultQuizFromPlan is the honest degradation: one self-assessed open
// question per knowledge node (stages when the map is empty), answers left
// blank rather than invented.
func defaultQuizFromPlan(plan *Plan) quizSpec {
	spec := quizSpec{Title: quizTitle(plan)}
	appendItem := func(nodeID, name string) {
		spec.Quizzes = append(spec.Quizzes, QuizItem{
			NodeID: nodeID, NodeName: name, Type: string(QuizShort),
			Question:    fmt.Sprintf("请用自己的话简述「%s」的核心要点（自评）。", name),
			Explanation: "（降级默认题：无 LLM 生成参考答案，请对照自学材料自评）",
		})
	}
	for _, n := range plan.Knowledge.Nodes {
		if len(spec.Quizzes) >= 20 {
			break
		}
		if n.Name == "" {
			continue
		}
		appendItem(n.SID, n.Name)
	}
	if len(spec.Quizzes) == 0 {
		for _, st := range plan.Stages {
			if len(spec.Quizzes) >= 10 {
				break
			}
			if st.Name == "" {
				continue
			}
			appendItem("", st.Name)
		}
	}
	if len(spec.Quizzes) == 0 {
		spec.Quizzes = []QuizItem{{Question: "（方案无知识点信息，无法生成测验）"}}
	}
	return spec
}

// Quizzes answers the plan's persisted set.
func (s *Service) Quizzes(planID string) (QuizSet, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return QuizSet{}, err
	}
	quizzes, err := s.store.Quizzes(planID)
	if err != nil {
		return QuizSet{}, err
	}
	return quizSet(quizTitle(plan), quizzes), nil
}

// Submit grades one attempt: choice on the server against the stored answer
// (unknown when the quiz carries none), every other type self-assessed. The
// attempt is recorded either way.
func (s *Service) Submit(planID, quizID, selected, text string) (map[string]any, error) {
	quizzes, err := s.store.Quizzes(planID)
	if err != nil {
		return nil, err
	}
	var quiz *Quiz
	for i := range quizzes {
		if quizzes[i].ID == quizID {
			quiz = &quizzes[i]
			break
		}
	}
	if quiz == nil {
		return nil, ErrNotFound
	}
	var correct *bool
	if quiz.Type == QuizChoice {
		correct = GradeChoice(quiz.Payload.Answer, selected)
	}
	attempt := QuizAttempt{
		QuizID: quizID, Selected: selected, Text: text,
		Correct: correct, At: s.opt.now(),
	}
	if err := s.store.AppendAttempt(planID, attempt); err != nil {
		return nil, err
	}
	var correctField any
	if correct != nil {
		correctField = *correct
	}
	return map[string]any{
		"correct": correctField, "answer": quiz.Payload.Answer,
		"explanation": quiz.Payload.Explanation,
	}, nil
}

// wrongQuizzes pairs every quiz with its latest attempt, keeping the ones
// whose latest attempt graded false — the wrong-book口径 both endpoints read.
func (s *Service) wrongQuizzes() ([]Quiz, []QuizAttempt, []string, error) {
	plans, err := s.store.Plans()
	if err != nil {
		return nil, nil, nil, err
	}
	var quizzes []Quiz
	var attempts []QuizAttempt
	var planIDs []string
	for _, summary := range plans {
		qs, err := s.store.Quizzes(summary.ID)
		if err != nil {
			continue
		}
		atts, err := s.store.Attempts(summary.ID)
		if err != nil {
			continue
		}
		latest := map[string]QuizAttempt{}
		for _, a := range atts {
			if prev, ok := latest[a.QuizID]; !ok || !a.At.Before(prev.At) {
				latest[a.QuizID] = a
			}
		}
		for i := range qs {
			a, ok := latest[qs[i].ID]
			if !ok || a.Correct == nil || *a.Correct {
				continue
			}
			quizzes = append(quizzes, qs[i])
			attempts = append(attempts, a)
			planIDs = append(planIDs, summary.ID)
		}
	}
	return quizzes, attempts, planIDs, nil
}

// WrongOverview aggregates the wrong-book per plan.
func (s *Service) WrongOverview() (map[string]any, error) {
	quizzes, _, planIDs, err := s.wrongQuizzes()
	if err != nil {
		return nil, err
	}
	counts := map[string]int{}
	titles := map[string]string{}
	for i, pid := range planIDs {
		counts[pid]++
		if _, seen := titles[pid]; !seen {
			if p, err := s.store.Plan(pid); err == nil {
				titles[pid] = p.Topic
			}
		}
		_ = quizzes[i]
	}
	type row struct {
		PlanID     string `json:"plan_id"`
		Topic      string `json:"topic"`
		WrongCount int    `json:"wrong_count"`
	}
	items := make([]row, 0, len(counts))
	total := 0
	for pid, count := range counts {
		items = append(items, row{PlanID: pid, Topic: titles[pid], WrongCount: count})
		total += count
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].WrongCount != items[j].WrongCount {
			return items[i].WrongCount > items[j].WrongCount
		}
		return items[i].PlanID < items[j].PlanID
	})
	return map[string]any{"items": items, "total": total}, nil
}

// WrongQueue lists the wrong quizzes newest-wrong-first, payloads intact so
// the client can re-answer and be graded again.
func (s *Service) WrongQueue(limit int) (map[string]any, error) {
	if limit <= 0 {
		limit = 100
	}
	if limit > 200 {
		limit = 200
	}
	quizzes, attempts, planIDs, err := s.wrongQuizzes()
	if err != nil {
		return nil, err
	}
	type paired struct {
		quiz    Quiz
		attempt QuizAttempt
		planID  string
	}
	pairs := make([]paired, 0, len(quizzes))
	for i := range quizzes {
		pairs = append(pairs, paired{quizzes[i], attempts[i], planIDs[i]})
	}
	sort.Slice(pairs, func(i, j int) bool { return pairs[i].attempt.At.After(pairs[j].attempt.At) })
	total := len(pairs)
	if len(pairs) > limit {
		pairs = pairs[:limit]
	}
	items := make([]map[string]any, 0, len(pairs))
	for _, p := range pairs {
		items = append(items, map[string]any{
			"id": p.quiz.ID, "plan_id": p.planID, "node_id": p.quiz.NodeID,
			"type": string(p.quiz.Type), "question": p.quiz.Payload.Question,
			"options": p.quiz.Payload.Options, "answer": p.quiz.Payload.Answer,
			"wrong_at": p.attempt.At,
		})
	}
	return map[string]any{"items": items, "total": total}, nil
}

// QuizStats summarizes one plan's attempts: counts by outcome and the
// correct rate over graded attempts.
func (s *Service) QuizStats(planID string) (map[string]any, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	attempts, err := s.store.Attempts(planID)
	if err != nil {
		return nil, err
	}
	graded, correct, wrong := 0, 0, 0
	for _, a := range attempts {
		if a.Correct == nil {
			continue
		}
		graded++
		if *a.Correct {
			correct++
		} else {
			wrong++
		}
	}
	rate := 0.0
	if graded > 0 {
		rate = float64(correct) / float64(graded)
	}
	return map[string]any{
		"total": len(attempts), "graded": graded, "correct": correct,
		"wrong": wrong, "self_assessed": len(attempts) - graded,
		"correct_rate": rate,
	}, nil
}
