package tutor

import (
	"crypto/rand"
	"encoding/base64"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"deep-tutor/internal/base/fileutil"
)

// ShareDefaultTTL is how long a share link lives unless its owner pins it
// open. A link that never expires is a link that cannot be forgotten: the
// owner's only remedy is to revoke it, and a leaked link is public by
// definition.
const ShareDefaultTTL = 7 * 24 * time.Hour

// Share is one read-only share link over a plan, keyed by an unguessable
// token. It is the file-canonical form of Deep Tutor's ShareLink row.
type Share struct {
	Token     string    `json:"token"`
	PlanID    string    `json:"plan_id"`
	CreatedAt time.Time `json:"created_at"`
	// ExpiresAt is when the link stops answering. Nil means the owner
	// pinned it open on purpose.
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
}

// expired reports whether the link's term is over at now.
func (sh *Share) expired(now time.Time) bool {
	return sh.ExpiresAt != nil && !now.Before(*sh.ExpiresAt)
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
	Title     string    `json:"title"`
	Topic     string    `json:"topic"`
	CreatedAt time.Time `json:"created_at"`
	// ExpiresAt is when the link stops answering; nil for a link the
	// owner pinned open.
	ExpiresAt *time.Time    `json:"expires_at,omitempty"`
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

// sharePath is the one place a share token's file name is built.
func (s *Store) sharePath(token string) string {
	return filepath.Join(s.sharesDir(), safeSegment(token)+".json")
}

func (s *Store) saveShare(sh *Share) error {
	return s.writeJSON(s.sharePath(sh.Token), sh)
}

func (s *Store) shareByToken(token string) (*Share, error) {
	var sh Share
	if err := s.readJSON(s.sharePath(token), &sh); err != nil {
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
	if err := fileutil.Remove(s.sharePath(token)); err != nil && !os.IsNotExist(err) {
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
// A new link runs for ShareDefaultTTL; the owner can pin it open with
// ShareMakePermanent.
func (s *Service) SharePlan(planID string) (*Share, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	if sh, err := s.store.shareByPlanID(planID); err != nil {
		return nil, err
	} else if sh != nil {
		if !sh.expired(s.opt.now()) {
			return sh, nil
		}
		// The old link's term is over: it is removed and a fresh one is
		// minted, so creating a share never hands back a dead link.
		if err := s.store.deleteShare(sh.Token); err != nil {
			return nil, err
		}
	}
	now := s.opt.now()
	expires := now.Add(ShareDefaultTTL)
	sh := &Share{Token: newShareToken(), PlanID: planID, CreatedAt: now, ExpiresAt: &expires}
	if err := s.store.saveShare(sh); err != nil {
		return nil, err
	}
	return sh, nil
}

// ShareMakePermanent pins the plan's share link open: the link stops
// expiring and stays answerable until it is revoked. It is the owner's
// explicit choice, not the default.
func (s *Service) ShareMakePermanent(planID string) (*Share, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	sh, err := s.store.shareByPlanID(planID)
	if err != nil {
		return nil, err
	}
	if sh == nil {
		return nil, ErrNotFound
	}
	sh.ExpiresAt = nil
	if err := s.store.saveShare(sh); err != nil {
		return nil, err
	}
	return sh, nil
}

// ShareStatus reports the plan's share, nil when the plan is not shared.
// An expired share is not a share: it is removed and reported as none.
func (s *Service) ShareStatus(planID string) (*Share, error) {
	if _, err := s.store.Plan(planID); err != nil {
		return nil, err
	}
	sh, err := s.store.shareByPlanID(planID)
	if err != nil {
		return nil, err
	}
	if sh == nil {
		return nil, nil
	}
	if sh.expired(s.opt.now()) {
		if err := s.store.deleteShare(sh.Token); err != nil {
			slog.Warn("tutor: the expired share was not removed", "token", sh.Token, "err", err)
		}
		return nil, nil
	}
	return sh, nil
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
// link would only ever answer "plan deleted". An expired share is deleted
// the same way — its term is over, so listing it would advertise a link
// that no longer answers.
func (s *Service) ShareList() ([]ShareEntry, error) {
	all, err := s.store.shares()
	if err != nil {
		return nil, err
	}
	now := s.opt.now()
	out := make([]ShareEntry, 0, len(all))
	for i := range all {
		if all[i].expired(now) {
			if err := s.store.deleteShare(all[i].Token); err != nil {
				slog.Warn("tutor: the expired share was not removed", "token", all[i].Token, "err", err)
			}
			continue
		}
		plan, err := s.store.Plan(all[i].PlanID)
		if err != nil {
			if derr := s.store.deleteShare(all[i].Token); derr != nil {
				slog.Warn("tutor: the orphaned share was not removed", "token", all[i].Token, "err", derr)
			}
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
// of its plan. Unknown, revoked and expired tokens answer ErrNotFound; a
// token whose plan disappeared has its share removed and answers the same.
func (s *Service) SharedPlanView(token string) (*SharedPlan, error) {
	token = strings.TrimSpace(token)
	if token == "" {
		return nil, ErrNotFound
	}
	sh, err := s.store.shareByToken(token)
	if err != nil {
		return nil, ErrNotFound
	}
	if sh.expired(s.opt.now()) {
		// The term is over: the link stops answering and is removed, so a
		// leaked one cannot be told apart from one that never existed.
		if err := s.store.deleteShare(sh.Token); err != nil {
			slog.Warn("tutor: the expired share was not removed", "token", sh.Token, "err", err)
		}
		return nil, ErrNotFound
	}
	plan, err := s.store.Plan(sh.PlanID)
	if err != nil {
		if derr := s.store.deleteShare(sh.Token); derr != nil {
			slog.Warn("tutor: the orphaned share was not removed", "token", sh.Token, "err", derr)
		}
		return nil, ErrNotFound
	}
	out := &SharedPlan{
		Title: plan.Title, Topic: plan.Topic, CreatedAt: plan.CreatedAt,
		ExpiresAt: sh.ExpiresAt,
		Intent:    plan.Intent, Knowledge: plan.Knowledge, Schedule: plan.Schedule,
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
