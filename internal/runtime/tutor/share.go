package tutor

import (
	"crypto/rand"
	"encoding/base64"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Share is one read-only share link over a plan, keyed by an unguessable
// token. It is the file-canonical form of Deep Tutor's ShareLink row.
type Share struct {
	Token     string    `json:"token"`
	PlanID    string    `json:"plan_id"`
	CreatedAt time.Time `json:"created_at"`
}

// ShareEntry is the management-list row: a share plus the plan it points at.
type ShareEntry struct {
	Share
	Topic string `json:"topic"`
	Title string `json:"title"`
}

// SharedResource is a plan resource as the public view carries it: the URL
// survives only when it is plain http(s), so a crafted scheme cannot run in
// the reader's browser context.
type SharedResource struct {
	Title    string       `json:"title"`
	URL      string       `json:"url"`
	Type     ResourceType `json:"type"`
	Verified VerifyLabel  `json:"verified"`
}

// SharedStage is a plan stage without any resource URL that failed the
// http(s) check.
type SharedStage struct {
	Index          int              `json:"index"`
	Name           string           `json:"name"`
	Goal           string           `json:"goal"`
	Difficulty     int              `json:"difficulty"`
	EstimatedWeeks float64          `json:"estimated_weeks"`
	Topics         []StageTopic     `json:"topics,omitempty"`
	Resources      []SharedResource `json:"resources,omitempty"`
	Milestone      string           `json:"milestone,omitempty"`
}

// SharedQuiz is a quiz with its answer side removed: the reader sees the
// question and options, never the answer or explanation.
type SharedQuiz struct {
	NodeID   string   `json:"node_id,omitempty"`
	Type     QuizType `json:"type"`
	Question string   `json:"question"`
	Options  []string `json:"options,omitempty"`
}

// SharedPlan is the public, sanitized projection of a plan. Generation
// metadata (model, tokens, cost) stays private: the share is learning
// content, not a usage report.
type SharedPlan struct {
	Title     string        `json:"title"`
	Topic     string        `json:"topic"`
	CreatedAt time.Time     `json:"created_at"`
	Intent    Intent        `json:"intent"`
	Knowledge KnowledgeMap  `json:"knowledge_map"`
	Stages    []SharedStage `json:"stages"`
	Schedule  []ScheduleDay `json:"schedule"`
	Quizzes   []SharedQuiz  `json:"quizzes,omitempty"`
}

// newShareToken mints the 192-bit url-safe token Deep Tutor used
// (secrets.token_urlsafe(24)): unguessable, no enumeration surface.
func newShareToken() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic("tutor: crypto/rand unavailable: " + err.Error())
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

func (s *Store) sharesDir() string { return filepath.Join(s.root, "shares") }

func (s *Store) saveShare(sh *Share) error {
	return s.writeJSON(filepath.Join(s.sharesDir(), sh.Token+".json"), sh)
}

func (s *Store) shareByToken(token string) (*Share, error) {
	var sh Share
	if err := s.readJSON(filepath.Join(s.sharesDir(), token+".json"), &sh); err != nil {
		return nil, err
	}
	return &sh, nil
}

func (s *Store) shares() ([]Share, error) {
	entries, err := os.ReadDir(s.sharesDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []Share
	for _, e := range entries {
		if e.IsDir() || strings.HasSuffix(e.Name(), ".tmp") {
			continue
		}
		sh, err := s.shareByToken(strings.TrimSuffix(e.Name(), ".json"))
		if err != nil {
			continue
		}
		out = append(out, *sh)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].CreatedAt.Equal(out[j].CreatedAt) {
			return out[i].Token < out[j].Token
		}
		return out[i].CreatedAt.After(out[j].CreatedAt)
	})
	return out, nil
}

func (s *Store) shareByPlanID(planID string) (*Share, error) {
	all, err := s.shares()
	if err != nil {
		return nil, err
	}
	for i := range all {
		if all[i].PlanID == planID {
			return &all[i], nil
		}
	}
	return nil, nil
}

func (s *Store) deleteShare(token string) error {
	if err := os.Remove(filepath.Join(s.sharesDir(), token+".json")); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// deleteSharesForPlan drops every share pointing at planID; the session-delete
// cascade calls it so a deleted plan leaves no live public link.
func (s *Store) deleteSharesForPlan(planID string) error {
	all, err := s.shares()
	if err != nil {
		return err
	}
	for i := range all {
		if all[i].PlanID == planID {
			if err := s.deleteShare(all[i].Token); err != nil {
				return err
			}
		}
	}
	return nil
}

// SharePlan creates the plan's share link, or returns the existing one:
// repeated calls are idempotent, matching Deep Tutor's create-or-return.
func (s *Service) SharePlan(planID string) (*Share, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	if sh, err := s.store.shareByPlanID(planID); err != nil {
		return nil, err
	} else if sh != nil {
		return sh, nil
	}
	sh := &Share{Token: newShareToken(), PlanID: planID, CreatedAt: s.opt.now()}
	if err := s.store.saveShare(sh); err != nil {
		return nil, err
	}
	return sh, nil
}

// ShareStatus reports the plan's share, nil when the plan is not shared.
func (s *Service) ShareStatus(planID string) (*Share, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	return s.store.shareByPlanID(planID)
}

// ShareRevoke removes the plan's share link if one exists; revoking an
// unshared plan is a no-op so the endpoint can answer 204 either way.
func (s *Service) ShareRevoke(planID string) error {
	if _, err := s.store.Plan(planID); err != nil {
		return err
	}
	sh, err := s.store.shareByPlanID(planID)
	if err != nil {
		return err
	}
	if sh == nil {
		return nil
	}
	return s.store.deleteShare(sh.Token)
}

// ShareList answers every live share with its plan's title, newest first.
// A share whose plan is gone is deleted on sight rather than listed: the
// link would only ever answer "plan deleted".
func (s *Service) ShareList() ([]ShareEntry, error) {
	all, err := s.store.shares()
	if err != nil {
		return nil, err
	}
	out := make([]ShareEntry, 0, len(all))
	for i := range all {
		plan, err := s.store.Plan(all[i].PlanID)
		if err != nil {
			_ = s.store.deleteShare(all[i].Token)
			continue
		}
		out = append(out, ShareEntry{Share: all[i], Title: plan.Title, Topic: plan.Topic})
	}
	return out, nil
}

// sharedHTTPScheme is the only scheme a shared resource URL may keep.
func sharedHTTPScheme(url string) bool {
	u := strings.ToLower(strings.TrimSpace(url))
	return strings.HasPrefix(u, "http://") || strings.HasPrefix(u, "https://")
}

func sharedStage(st Stage) SharedStage {
	out := SharedStage{
		Index: st.Index, Name: st.Name, Goal: st.Goal, Difficulty: st.Difficulty,
		EstimatedWeeks: st.EstimatedWeeks, Topics: st.Topics, Milestone: st.Milestone,
	}
	for _, r := range st.Resources {
		url := r.URL
		if !sharedHTTPScheme(url) {
			url = ""
		}
		out.Resources = append(out.Resources, SharedResource{
			Title: r.Title, URL: url, Type: r.Type, Verified: r.Verified,
		})
	}
	return out
}

// SharedPlanView resolves a share token to the public, sanitized projection
// of its plan. Unknown and revoked tokens answer ErrNotFound; a token whose
// plan disappeared has its share removed and answers the same.
func (s *Service) SharedPlanView(token string) (*SharedPlan, error) {
	token = strings.TrimSpace(token)
	if token == "" {
		return nil, ErrNotFound
	}
	sh, err := s.store.shareByToken(token)
	if err != nil {
		return nil, ErrNotFound
	}
	plan, err := s.store.Plan(sh.PlanID)
	if err != nil {
		_ = s.store.deleteShare(sh.Token)
		return nil, ErrNotFound
	}
	out := &SharedPlan{
		Title: plan.Title, Topic: plan.Topic, CreatedAt: plan.CreatedAt,
		Intent: plan.Intent, Knowledge: plan.Knowledge, Schedule: plan.Schedule,
	}
	for _, st := range plan.Stages {
		out.Stages = append(out.Stages, sharedStage(st))
	}
	quizzes, err := s.store.Quizzes(plan.ID)
	if err != nil {
		return nil, err
	}
	for _, q := range quizzes {
		out.Quizzes = append(out.Quizzes, SharedQuiz{
			NodeID: q.NodeID, Type: q.Type, Question: q.Payload.Question, Options: q.Payload.Options,
		})
	}
	return out, nil
}
