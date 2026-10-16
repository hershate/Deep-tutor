package serve

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"reasonix/internal/platform/gitcommit"
	"reasonix/internal/session/control"
)

func (s *Server) registerGitRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /changes", s.changes)
	mux.HandleFunc("GET /changes/diff", s.changeDiff)
	mux.HandleFunc("GET /workspace/git", s.workspaceGit)
	mux.HandleFunc("GET /workspace/git/log", s.gitHistory)
	mux.HandleFunc("GET /workspace/branches", s.workspaceBranches)
	mux.HandleFunc("POST /workspace/branch/switch", s.workspaceSwitchBranch)
	mux.HandleFunc("POST /commit/propose", s.commitPropose)
	mux.HandleFunc("POST /commit", s.commitStaged)
	mux.HandleFunc("POST /changes/stage", s.stageChanges)
}

// commitPropose answers the staged changes and a model-proposed message. It
// changes nothing in the repository.
func (s *Server) commitPropose(w http.ResponseWriter, r *http.Request) {
	p, err := s.ctl().ProposeCommit(r.Context())
	if err != nil {
		refuseCommit(w, r, err)
		return
	}
	writeJSON(w, p)
}

// commitStaged records a local commit of the staged set the person confirmed.
func (s *Server) commitStaged(w http.ResponseWriter, r *http.Request) {
	var req control.CommitRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, 2*gitcommit.MaxMessageBytes)).Decode(&req); err != nil {
		refuse(w, http.StatusBadRequest, "commit.bad_request", "the request body is not a JSON commit", nil)
		return
	}
	res, err := s.ctl().CommitStaged(r.Context(), req)
	if err != nil {
		refuseCommit(w, r, err)
		return
	}
	writeJSON(w, res)
}

// stageChanges moves the named changes into the index, or out of it. Only paths
// the workspace lists as changed are accepted.
func (s *Server) stageChanges(w http.ResponseWriter, r *http.Request) {
	var req control.StageRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		refuse(w, http.StatusBadRequest, "stage.bad_request", "the request body is not a JSON stage request", nil)
		return
	}
	if err := s.ctl().StageChanges(r.Context(), req); err != nil {
		switch {
		case r.Context().Err() != nil:
			// The person cancelled; nobody is left to read an answer.
		case errors.Is(err, gitcommit.ErrNoRepository):
			refuse(w, http.StatusConflict, "commit.no_repository", err.Error(), nil)
		case errors.Is(err, gitcommit.ErrUnknownPath):
			refuse(w, http.StatusBadRequest, "stage.unknown_path", err.Error(), nil)
		default:
			refuse(w, http.StatusInternalServerError, "stage.failed", err.Error(), nil)
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func refuseCommit(w http.ResponseWriter, r *http.Request, err error) {
	var sensitive *gitcommit.SensitiveError
	switch {
	case r.Context().Err() != nil:
		// The person cancelled; nobody is left to read an answer.
	case errors.Is(err, gitcommit.ErrNoRepository):
		refuse(w, http.StatusConflict, "commit.no_repository", err.Error(), nil)
	case errors.Is(err, gitcommit.ErrNothingStaged):
		refuse(w, http.StatusConflict, "commit.nothing_staged", err.Error(), nil)
	case errors.Is(err, gitcommit.ErrStagedChanged):
		refuse(w, http.StatusConflict, "commit.staged_changed", err.Error(), nil)
	case errors.As(err, &sensitive):
		refuse(w, http.StatusConflict, "commit.secrets_staged", err.Error(),
			map[string]any{"files": sensitive.Files, "content": sensitive.Content})
	case errors.Is(err, gitcommit.ErrEmptyMessage):
		refuse(w, http.StatusBadRequest, "commit.empty_message", err.Error(), nil)
	case errors.Is(err, gitcommit.ErrMessageInvalid):
		refuse(w, http.StatusBadRequest, "commit.message_invalid", err.Error(), nil)
	case errors.Is(err, gitcommit.ErrMessageTooLong):
		refuse(w, http.StatusRequestEntityTooLarge, "commit.message_too_long", err.Error(),
			map[string]any{"max_bytes": gitcommit.MaxMessageBytes})
	case errors.Is(err, gitcommit.ErrIdentityMissing):
		refuse(w, http.StatusConflict, "commit.identity_missing", err.Error(), nil)
	case errors.Is(err, gitcommit.ErrCommitFailed):
		refuse(w, http.StatusInternalServerError, "commit.failed", err.Error(), nil)
	default:
		refuse(w, http.StatusInternalServerError, "commit.git_failed", err.Error(), nil)
	}
}
