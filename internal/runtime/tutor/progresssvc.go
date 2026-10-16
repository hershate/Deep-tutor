package tutor

import (
	"sort"
	"time"
)

// ProgressNode is one node in a plan's progress overview: the plan's node
// joined with its progress row, not_started rows synthesized for untouched
// nodes.
type ProgressNode struct {
	NodeID     string         `json:"node_id"`
	Name       string         `json:"name"`
	Status     ProgressStatus `json:"status"`
	Mastery    float64        `json:"mastery"`
	NextReview string         `json:"next_review,omitempty"`
}

// ProgressOverview is GET /tutor/plans/{id}/progress: the per-node rows plus
// the aggregates the dashboard reads.
type ProgressOverview struct {
	Nodes      []ProgressNode `json:"nodes"`
	MasteryAvg float64        `json:"mastery_avg"`
	DoneCount  int            `json:"done_count"`
	Total      int            `json:"total"`
}

// RecordProgress is Deep-tutor's POST /progress: an idempotent overwrite of
// one node's row — mastery from the SM-2-lite formula, next review derived
// from it.
func (s *Service) RecordProgress(planID, nodeID string, status ProgressStatus, quizScore *float64) (map[string]any, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return nil, err
	}
	switch status {
	case ProgressNotStarted, ProgressLearning, ProgressDone:
	default:
		return nil, ErrNotFound // the caller's status vocabulary is malformed
	}
	today := s.opt.now()
	list, err := s.store.ProgressList(planID)
	if err != nil {
		return nil, err
	}
	var row *Progress
	for i := range list {
		if list[i].NodeID == nodeID {
			row = &list[i]
			break
		}
	}
	var mastery float64
	nextStr := func(m float64) string { return NextReviewDate(m, today).Format("2006-01-02") }
	if row == nil {
		mastery = ComputeMastery(0, status, quizScore)
		list = append(list, Progress{
			NodeID: nodeID, Status: status, Mastery: mastery,
			NextReview: nextStr(mastery), UpdatedAt: today,
		})
	} else {
		row.Mastery = ComputeMastery(row.Mastery, status, quizScore)
		row.Status = status
		row.NextReview = nextStr(row.Mastery)
		row.UpdatedAt = today
		mastery = row.Mastery
	}
	if err := s.store.SaveProgress(planID, list); err != nil {
		return nil, err
	}
	var name string
	for _, n := range plan.Knowledge.Nodes {
		if n.SID == nodeID {
			name = n.Name
			break
		}
	}
	return map[string]any{
		"node_id": nodeID, "name": name, "status": string(status),
		"mastery": mastery, "next_review": nextStr(mastery),
	}, nil
}

// ProgressOf answers one plan's overview.
func (s *Service) ProgressOf(planID string) (*ProgressOverview, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.ProgressList(planID)
	if err != nil {
		return nil, err
	}
	byNode := map[string]Progress{}
	for _, r := range rows {
		byNode[r.NodeID] = r
	}
	overview := &ProgressOverview{Nodes: make([]ProgressNode, 0, len(plan.Knowledge.Nodes))}
	sum, done := 0.0, 0
	for _, n := range plan.Knowledge.Nodes {
		node := ProgressNode{NodeID: n.SID, Name: n.Name, Status: ProgressNotStarted}
		if row, ok := byNode[n.SID]; ok {
			node.Status = row.Status
			node.Mastery = row.Mastery
			node.NextReview = row.NextReview
		}
		if node.Status == ProgressDone {
			done++
		}
		sum += node.Mastery
		overview.Nodes = append(overview.Nodes, node)
	}
	overview.DoneCount = done
	overview.Total = len(overview.Nodes)
	if overview.Total > 0 {
		overview.MasteryAvg = round3(sum / float64(overview.Total))
	}
	return overview, nil
}

func round3(v float64) float64 { return float64(int(v*1000+0.5)) / 1000 }

// ReviewItem is one due node, the shared shape of /review/due and
// /review/queue (the queue enriches with difficulty, must-learn, note).
type ReviewItem struct {
	PlanID     string  `json:"plan_id"`
	Topic      string  `json:"plan_topic"`
	NodeID     string  `json:"node_id"`
	Name       string  `json:"name"`
	Status     string  `json:"status"`
	Mastery    float64 `json:"mastery"`
	NextReview string  `json:"next_review"`
	Difficulty int     `json:"difficulty,omitempty"`
	MustLearn  bool    `json:"must_learn,omitempty"`
	Note       string  `json:"note,omitempty"`
}

func (s *Service) dueItems() []ReviewItem {
	plans, err := s.store.Plans()
	if err != nil {
		return nil
	}
	today := s.opt.now().Format("2006-01-02")
	var items []ReviewItem
	for _, summary := range plans {
		plan, err := s.store.Plan(summary.ID)
		if err != nil {
			continue
		}
		rows, err := s.store.ProgressList(summary.ID)
		if err != nil {
			continue
		}
		byNode := map[string]Progress{}
		for _, r := range rows {
			byNode[r.NodeID] = r
		}
		for _, n := range plan.Knowledge.Nodes {
			row, ok := byNode[n.SID]
			if !ok || row.NextReview == "" || row.NextReview > today {
				continue
			}
			if row.Status == ProgressDone && row.Mastery >= 1 {
				continue
			}
			items = append(items, ReviewItem{
				PlanID: summary.ID, Topic: plan.Topic, NodeID: n.SID, Name: n.Name,
				Status: string(row.Status), Mastery: row.Mastery, NextReview: row.NextReview,
			})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].NextReview != items[j].NextReview {
			return items[i].NextReview < items[j].NextReview
		}
		return items[i].NodeID < items[j].NodeID
	})
	return items
}

// ReviewDue lists the due nodes across every plan, most overdue first.
func (s *Service) ReviewDue(limit int) map[string]any {
	if limit <= 0 {
		limit = 100
	}
	if limit > 500 {
		limit = 500
	}
	items := s.dueItems()
	total := len(items)
	if len(items) > limit {
		items = items[:limit]
	}
	// An empty due list still renders as an array: nil would reach the page
	// as JSON null and break its list handling.
	if items == nil {
		items = []ReviewItem{}
	}
	return map[string]any{"items": items, "total": total}
}

// ReviewQueue enriches the due list with the node facts the flashcard back
// shows. Node notes arrive with the Studio surface; an empty note leaves the
// card on its self-assessment prompt.
func (s *Service) ReviewQueue(limit int) map[string]any {
	if limit <= 0 {
		limit = 100
	}
	if limit > 500 {
		limit = 500
	}
	base := s.dueItems()
	total := len(base)
	if len(base) > limit {
		base = base[:limit]
	}
	out := make([]ReviewItem, 0, len(base))
	for _, it := range base {
		if plan, err := s.store.Plan(it.PlanID); err == nil {
			for _, n := range plan.Knowledge.Nodes {
				if n.SID == it.NodeID {
					it.Difficulty = n.Difficulty
					it.MustLearn = n.MustLearn
					break
				}
			}
		}
		out = append(out, it)
	}
	return map[string]any{"items": out, "total": total}
}

// AllLogs gathers learning logs across plans, newest first.
func (s *Service) AllLogs() ([]LearningLog, error) {
	plans, err := s.store.Plans()
	if err != nil {
		return nil, err
	}
	var out []LearningLog
	for _, summary := range plans {
		logs, err := s.store.Logs(summary.ID)
		if err != nil {
			continue
		}
		out = append(out, logs...)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.After(out[j].CreatedAt) })
	return out, nil
}

// StatsSummary aggregates the check-in logs: minutes total and today, plus
// the day streak ending today (or yesterday, so one missed day does not read
// as zero history).
type StatsSummary struct {
	TotalMinutes int `json:"total_minutes"`
	TodayMinutes int `json:"today_minutes"`
	StreakDays   int `json:"streak_days"`
	CheckinDays  int `json:"checkin_days"`
}

func (s *Service) StatsSummary() (*StatsSummary, error) {
	logs, err := s.AllLogs()
	if err != nil {
		return nil, err
	}
	today := s.opt.now().Format("2006-01-02")
	byDate := map[string]int{}
	for _, l := range logs {
		byDate[l.Date] += l.Minutes
	}
	summary := &StatsSummary{TodayMinutes: byDate[today], CheckinDays: len(byDate)}
	for _, minutes := range byDate {
		summary.TotalMinutes += minutes
	}
	streakFrom := func(day time.Time) int {
		streak := 0
		for {
			key := day.Format("2006-01-02")
			if _, ok := byDate[key]; !ok {
				return streak
			}
			streak++
			day = day.AddDate(0, 0, -1)
		}
	}
	now := s.opt.now()
	summary.StreakDays = streakFrom(now)
	if summary.StreakDays == 0 {
		summary.StreakDays = streakFrom(now.AddDate(0, 0, -1))
	}
	return summary, nil
}

// StatsCalendar buckets minutes per day for the heatmap.
func (s *Service) StatsCalendar() (map[string]int, error) {
	logs, err := s.AllLogs()
	if err != nil {
		return nil, err
	}
	calendar := map[string]int{}
	for _, l := range logs {
		calendar[l.Date] += l.Minutes
	}
	return calendar, nil
}

// StatsByPlan reports minutes and mastery per plan.
func (s *Service) StatsByPlan() ([]map[string]any, error) {
	plans, err := s.store.Plans()
	if err != nil {
		return nil, err
	}
	out := make([]map[string]any, 0, len(plans))
	for _, summary := range plans {
		logs, err := s.store.Logs(summary.ID)
		if err != nil {
			logs = nil
		}
		minutes := 0
		for _, l := range logs {
			minutes += l.Minutes
		}
		entry := map[string]any{
			"plan_id": summary.ID, "topic": summary.Topic, "minutes": minutes,
		}
		if overview, err := s.ProgressOf(summary.ID); err == nil {
			entry["mastery_avg"] = overview.MasteryAvg
			entry["done_count"] = overview.DoneCount
			entry["total_nodes"] = overview.Total
		}
		out = append(out, entry)
	}
	return out, nil
}

// AddLog records one study check-in; the store mints ids and timestamps.
func (s *Service) AddLog(log *LearningLog) error {
	if _, err := s.store.Plan(log.PlanID); err != nil {
		return err
	}
	if log.Date == "" {
		log.Date = s.opt.now().Format("2006-01-02")
	}
	return s.store.AddLog(log)
}

func (s *Service) Logs(planID string) ([]LearningLog, error) {
	if planID != "" {
		if _, err := s.store.Plan(planID); err != nil {
			return nil, err
		}
		return s.store.Logs(planID)
	}
	return s.AllLogs()
}

func (s *Service) DeleteLog(planID, logID string) error { return s.store.DeleteLog(planID, logID) }
