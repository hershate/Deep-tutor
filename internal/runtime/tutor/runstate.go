package tutor

import (
	"path/filepath"
	"time"
)

// RunState is the persisted half-run: everything a clarify continuation, a
// retry, or a scoped regeneration needs to rebuild the pipeline's inputs.
// Deep-tutor keeps this inside the orchestrator state row; the file store
// gives it a file of its own.
type RunState struct {
	Trace      uint64                `json:"trace"`
	RawText    string                `json:"raw_text,omitempty"`
	Answers    []string              `json:"answers,omitempty"`
	TimeFields IntentArgs            `json:"time_fields,omitempty"`
	Outputs    map[string]ToolOutput `json:"outputs,omitempty"`
	Status     Status                `json:"status"`
	LastError  string                `json:"last_error,omitempty"`
	PlanID     string                `json:"plan_id,omitempty"`
	Cost       float64               `json:"cost,omitempty"`
	UpdatedAt  time.Time             `json:"updated_at"`
}

// SaveRunState persists the run state for one session, reviving typed tool
// data on the way back in so continuations see the same shapes a fresh run
// would have produced.
func (s *Store) SaveRunState(sessionID string, rs *RunState) error {
	rs.UpdatedAt = time.Now()
	return s.writeJSON(filepath.Join(s.sessionDir(sessionID), "run.json"), rs)
}

// RunStateOf loads the persisted run state; ErrNotFound when the session
// never ran.
func (s *Store) RunStateOf(sessionID string) (*RunState, error) {
	var rs RunState
	if err := s.readJSON(filepath.Join(s.sessionDir(sessionID), "run.json"), &rs); err != nil {
		return nil, err
	}
	rs.Outputs = ReviveOutputs(rs.Outputs)
	return &rs, nil
}

// ReviveOutputs restores typed tool data after a JSON round trip.
func ReviveOutputs(outputs map[string]ToolOutput) map[string]ToolOutput {
	if outputs == nil {
		return nil
	}
	for name, out := range outputs {
		outputs[name] = reviveOutput(name, out)
	}
	return outputs
}

// parseUpstreamFromStore rebuilds the parse_material output from the
// session's confirmed materials, so a fresh generate starts at
// understand_intent exactly like Deep-tutor's _start_full_generation.
func (s *Store) parseUpstreamFromStore(sessionID string) map[string]ToolOutput {
	out, ok := s.materialsParsedOutput(sessionID)
	if !ok {
		return nil
	}
	return map[string]ToolOutput{ToolParseMaterial: out}
}
