package tutor

import "time"

type MaterialKind string

const (
	MaterialFile  MaterialKind = "file"
	MaterialText  MaterialKind = "text"
	MaterialURL   MaterialKind = "url"
	MaterialImage MaterialKind = "image"
	MaterialAudio MaterialKind = "audio"
	MaterialVideo MaterialKind = "video"
)

type VerifyLabel string

const (
	VerifyOK        VerifyLabel = "ok"
	VerifyUncertain VerifyLabel = "uncertain"
	VerifyInvalid   VerifyLabel = "invalid"
)

type ResourceType string

const (
	ResourceTutorial ResourceType = "tutorial"
	ResourceVideo    ResourceType = "video"
	ResourceDoc      ResourceType = "doc"
	ResourceBook     ResourceType = "book"
)

type QuizType string

const (
	QuizChoice QuizType = "choice"
	QuizFill   QuizType = "fill"
	QuizShort  QuizType = "short"
	QuizEssay  QuizType = "essay"
)

type ProgressStatus string

const (
	ProgressNotStarted ProgressStatus = "not_started"
	ProgressLearning   ProgressStatus = "learning"
	ProgressDone       ProgressStatus = "done"
)

// Status is the generation-session state machine, mirroring Deep-tutor's
// OrchestratorStatus.
type Status string

const (
	StatusIdle          Status = "idle"
	StatusRunning       Status = "running"
	StatusPausedClarify Status = "paused_clarify"
	StatusDone          Status = "done"
	StatusError         Status = "error"
	StatusInterrupted   Status = "interrupted"
)

type Intent struct {
	Topic            string   `json:"topic"`
	Level            string   `json:"level,omitempty"`
	Goal             string   `json:"goal,omitempty"`
	DailyMinutes     int      `json:"daily_minutes,omitempty"`
	Deadline         string   `json:"deadline,omitempty"`
	StartDate        string   `json:"start_date,omitempty"`
	NeedsClarify     bool     `json:"needs_clarify"`
	ClarifyQuestions []string `json:"clarify_questions,omitempty"`
}

type KnowledgeNode struct {
	SID        string `json:"id"`
	Name       string `json:"name"`
	Difficulty int    `json:"difficulty"`
	MustLearn  bool   `json:"must_learn"`
	ParentID   string `json:"parent_id,omitempty"`
}

type KnowledgeEdge struct {
	From     string `json:"from"`
	To       string `json:"to"`
	Relation string `json:"relation,omitempty"`
}

type KnowledgeMap struct {
	Nodes []KnowledgeNode `json:"nodes"`
	Edges []KnowledgeEdge `json:"edges,omitempty"`
	Note  string          `json:"_note,omitempty"`
}

type StageTopic struct {
	Name      string `json:"name"`
	Why       string `json:"why,omitempty"`
	MustLearn bool   `json:"must_learn"`
}

type Exercise struct {
	Desc     string  `json:"desc"`
	EstHours float64 `json:"est_hours"`
}

type Resource struct {
	StageIndex int          `json:"stage_index"`
	Title      string       `json:"title"`
	URL        string       `json:"url"`
	Type       ResourceType `json:"type"`
	Verified   VerifyLabel  `json:"verified"`
	Note       string       `json:"note,omitempty"`
	// Snippet carries the fetched body prefix to the screening gate; it is
	// stripped before a resource is persisted into a plan.
	Snippet string `json:"-"`
}

type Stage struct {
	Index          int          `json:"index"`
	Name           string       `json:"name"`
	Goal           string       `json:"goal"`
	Difficulty     int          `json:"difficulty"`
	EstimatedWeeks float64      `json:"estimated_weeks"`
	Topics         []StageTopic `json:"topics"`
	Resources      []Resource   `json:"resources,omitempty"`
	Exercises      []Exercise   `json:"exercises,omitempty"`
	Milestone      string       `json:"milestone"`
}

// ScheduleData is schedule_plan's output: the day list plus the derived
// capacity fields CheckSchedule attaches.
type ScheduleData struct {
	Schedule        []ScheduleDay `json:"schedule"`
	Truncated       bool          `json:"truncated,omitempty"`
	StartDate       string        `json:"start_date,omitempty"`
	TotalMinutes    int           `json:"total_minutes,omitempty"`
	RequiredDays    int           `json:"required_days,omitempty"`
	SpanDays        int           `json:"span_days,omitempty"`
	CapacityMinutes int           `json:"capacity_minutes,omitempty"`
	Warning         string        `json:"warning,omitempty"`
	Suggestion      string        `json:"suggestion,omitempty"`
}

type ScheduleDay struct {
	Date       string   `json:"date"`
	StageIndex int      `json:"stage_index"`
	Tasks      []string `json:"tasks"`
	EstMinutes int      `json:"est_minutes"`
}

type CheckResult struct {
	Status  string `json:"status"` // pass | warn | fail
	Details string `json:"details,omitempty"`
}

type ValidationReport struct {
	DependencyClosure    CheckResult `json:"dependency_closure"`
	DifficultyStep       CheckResult `json:"difficulty_step"`
	CognitiveLoad        CheckResult `json:"cognitive_load"`
	MilestoneFeasibility CheckResult `json:"milestone_feasibility"`
	GapAnalysis          CheckResult `json:"gap_analysis"`
	Passed               bool        `json:"passed"`
	Fixes                []string    `json:"fixes,omitempty"`
	Degraded             bool        `json:"degraded,omitempty"`
}

type GenMeta struct {
	Model       string  `json:"model,omitempty"`
	TotalTokens int64   `json:"total_tokens,omitempty"`
	Cost        float64 `json:"cost,omitempty"`
	DurationMs  int64   `json:"duration_ms,omitempty"`
}

type Plan struct {
	ID         string            `json:"id"`
	SessionID  string            `json:"session_id"`
	Version    int               `json:"version"`
	Title      string            `json:"title"`
	Topic      string            `json:"topic"`
	CreatedAt  time.Time         `json:"created_at"`
	UpdatedAt  time.Time         `json:"updated_at"`
	Intent     Intent            `json:"intent"`
	Knowledge  KnowledgeMap      `json:"knowledge_map"`
	Stages     []Stage           `json:"stages"`
	Schedule   []ScheduleDay     `json:"schedule"`
	Validation *ValidationReport `json:"validation_report,omitempty"`
	Meta       GenMeta           `json:"metadata"`
}

type QuizPayload struct {
	Question    string   `json:"question"`
	Options     []string `json:"options,omitempty"`
	Answer      string   `json:"answer,omitempty"`
	Explanation string   `json:"explanation,omitempty"`
}

type Quiz struct {
	ID        string      `json:"id"`
	PlanID    string      `json:"plan_id"`
	NodeID    string      `json:"node_id"`
	Type      QuizType    `json:"type"`
	Payload   QuizPayload `json:"payload"`
	CreatedAt time.Time   `json:"created_at"`
}

// QuizAttempt keeps one submission. Correct is nil for self-assessed types;
// a choice quiz without a stored answer also grades nil (unknown), never
// true, so an empty submission cannot inflate mastery.
type QuizAttempt struct {
	QuizID   string    `json:"quiz_id"`
	Selected string    `json:"selected,omitempty"`
	Text     string    `json:"text,omitempty"`
	Correct  *bool     `json:"correct"`
	At       time.Time `json:"at"`
}

type Progress struct {
	NodeID     string         `json:"node_id"`
	Status     ProgressStatus `json:"status"`
	Mastery    float64        `json:"mastery"`
	NextReview string         `json:"next_review,omitempty"`
	UpdatedAt  time.Time      `json:"updated_at"`
}

type LearningLog struct {
	ID        string    `json:"id"`
	PlanID    string    `json:"plan_id"`
	Date      string    `json:"date"`
	Minutes   int       `json:"minutes"`
	Note      string    `json:"note,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}

type Note struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	Body      string    `json:"body"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// Material is one uploaded, pasted, or fetched learning input. Parsing is a
// separate, user-confirmed step, so status tracks the three-phase flow.
type Material struct {
	ID        string            `json:"id"`
	SessionID string            `json:"session_id"`
	Kind      MaterialKind      `json:"kind"`
	Source    string            `json:"source"`               // filename, URL, or "text"
	ParseMode string            `json:"parse_mode,omitempty"` // full | fast | none
	Status    string            `json:"status"`               // pending | confirmed | parsed
	Chars     int               `json:"chars,omitempty"`
	Error     string            `json:"error,omitempty"`
	Meta      map[string]string `json:"meta,omitempty"` // provenance (e.g. kb_document_id)
	CreatedAt time.Time         `json:"created_at"`
}
