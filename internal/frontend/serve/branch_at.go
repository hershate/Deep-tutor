package serve

import (
	"encoding/json"
	"errors"
	"net/http"

	"reasonix/internal/session/control"
)

// branchAt copies the conversation up to the end of one turn into a new session
// and answers with its path; this controller stays on its own session, so the
// frontend opens the branch beside it.
func (s *Server) branchAt(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Turn *int   `json:"turn"`
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Turn == nil || *body.Turn < 0 {
		missingField(w, "turn")
		return
	}
	path, err := s.ctl().BranchAt(*body.Turn, body.Name)
	if err != nil {
		refuseBranchAt(w, err)
		return
	}
	writeJSON(w, struct {
		Path string `json:"path"`
	}{Path: path})
}

func refuseBranchAt(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, control.ErrTurnRunning):
		busy(w, "conversation_branch.turn_running", err.Error(), nil)
	case errors.Is(err, control.ErrBranchUnavailable):
		refuse(w, http.StatusConflict, "conversation_branch.unavailable", err.Error(), nil)
	default:
		refuse(w, http.StatusInternalServerError, "conversation_branch.failed", err.Error(), nil)
	}
}
