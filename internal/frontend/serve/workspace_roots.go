package serve

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

type workspaceRootsView struct {
	Primary string   `json:"primary"`
	Extra   []string `json:"extra"`
	// Pending counts panes on this workspace that could not be rebuilt now
	// because they were busy; they take the change at their next rebuild.
	Pending int `json:"pending,omitempty"`
}

func rootsView(r control.WorkspaceRoots) workspaceRootsView {
	if r.Extra == nil {
		r.Extra = []string{}
	}
	return workspaceRootsView{Primary: r.Primary, Extra: r.Extra}
}

func (s *Server) workspaceRoots(w http.ResponseWriter, _ *http.Request) {
	roots, err := s.ctl().WorkspaceRoots()
	if err != nil {
		refuse(w, http.StatusConflict, "project_grants.unavailable", "workspace folders could not be read", nil)
		return
	}
	writeJSON(w, rootsView(roots))
}

// rebuildSiblings applies the grant to every other pane on the workspace, so
// withdrawing a root narrows them all. A pane that is running keeps its old
// roots until its next rebuild, and is counted.
func (s *Server) rebuildSiblings(ctx context.Context) int {
	if s.siblings == nil {
		return 0
	}
	pending := 0
	for _, sib := range s.siblings(s) {
		if err := sib.rebuildInPlace(ctx); err != nil {
			pending++
		}
	}
	return pending
}

func (s *Server) addWorkspaceRoot(w http.ResponseWriter, r *http.Request) {
	s.editWorkspaceRoots(w, r, true, func(ctrl control.SessionAPI, path string) error {
		_, err := ctrl.AddWorkspaceRoot(path)
		return err
	})
}

func (s *Server) removeWorkspaceRoot(w http.ResponseWriter, r *http.Request) {
	s.editWorkspaceRoots(w, r, false, func(ctrl control.SessionAPI, path string) error { return ctrl.RemoveWorkspaceRoot(path) })
}

// Adding a root widens where tools may write, so a network-reachable kernel
// needs the same grant as sandbox editing; taking one away narrows and is always
// the reader's to do. The runtime is rebuilt on the result.
func (s *Server) editWorkspaceRoots(w http.ResponseWriter, r *http.Request, widens bool, apply func(control.SessionAPI, string) error) {
	if widens && !s.grants.at(r).providerEdit {
		refuse(w, http.StatusForbidden, "workspace_roots.editing_disabled", "workspace folder editing is not enabled on this server", nil)
		return
	}
	var body struct {
		Path string `json:"path"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8<<10)).Decode(&body); err != nil {
		badBody(w)
		return
	}
	if strings.TrimSpace(body.Path) == "" {
		refuse(w, http.StatusBadRequest, "workspace_roots.path_required", "path is required", nil)
		return
	}
	// Only this pane's own lock is held while it rebuilds; the others are
	// rebuilt after it is released, so two panes editing at once cannot each
	// wait on the other's.
	applied := func() bool {
		s.bindMu.Lock()
		defer s.bindMu.Unlock()
		ctrl := s.ctl()
		if controllerHasActiveRuntimeWork(ctrl) {
			busy(w, "workspace_roots.busy", "finish the current work before changing the workspace folders", nil)
			return false
		}
		model := currentModelRef(ctrl)
		if model == "" {
			refuse(w, http.StatusConflict, "workspace_roots.rebuild_unavailable", "no current model to rebuild", nil)
			return false
		}
		if err := apply(ctrl, body.Path); err != nil {
			var refusal *control.WorkspaceRootError
			switch {
			case errors.As(err, &refusal):
				refuse(w, http.StatusBadRequest, string(refusal.Code), "this folder cannot be added", nil)
			case errors.Is(err, config.ErrProjectGrantsUnavailable):
				refuse(w, http.StatusConflict, "project_grants.unavailable", "workspace folders could not be updated", nil)
			default:
				writeErr(w, http.StatusInternalServerError, err)
			}
			return false
		}
		if err := s.switchModelLocked(r.Context(), model); err != nil {
			rebuildFailed(w, err)
			return false
		}
		return true
	}()
	if !applied {
		return
	}
	pending := s.rebuildSiblings(r.Context())
	roots, err := s.ctl().WorkspaceRoots()
	if err != nil {
		refuse(w, http.StatusConflict, "project_grants.unavailable", "workspace folders could not be read", nil)
		return
	}
	view := rootsView(roots)
	view.Pending = pending
	writeJSON(w, view)
}
