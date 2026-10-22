// hub_sessions.go — which session a new pane drives, and which folder it opens
// against.
//
// A pane can be asked for two things at once: a transcript it should continue
// and a workspace it should live in. Both answers come from what the hub
// already holds, so they live together: a reader looking for "where does this
// pane go" has one file to open, not two.
package serve

import (
	"errors"
	"net/http"
	"os"
	"strings"

	"deep-tutor/internal/state/sessionstore"
)

// findSession returns the runtime already driving path. Session paths reach us
// spelled more than one way, so compare them canonically — binding a second
// writer to one transcript is what forks a recovery branch on every save.
func (h *Hub) findSession(path string) *Runtime {
	path = sessionstore.CanonicalSessionPath(strings.TrimSpace(path))
	if path == "" {
		return nil
	}
	h.mu.RLock()
	defer h.mu.RUnlock()
	for _, id := range h.order {
		rt := h.runtimes[id]
		// A remote transcript names a file on another machine: it cannot
		// collide with a local one, and deduplicating it is that kernel's job.
		if rt != nil && rt.remote == nil && sessionstore.CanonicalSessionPath(rt.Server.Controller().SessionPath()) == path {
			return rt
		}
	}
	return nil
}

// resolveRoot decides which folder a new runtime opens against: the one asked
// for, the one that owns the session being opened, or the first runtime's.
func (h *Hub) resolveRoot(req OpenRequest) (string, error) {
	if root := strings.TrimSpace(req.Root); root != "" {
		return resolveWorkspaceDir(root)
	}
	if path := strings.TrimSpace(req.SessionPath); path != "" {
		if root := workspaceRootForSession(path); root != "" {
			return root, nil
		}
	}
	h.mu.RLock()
	first := ""
	if len(h.order) > 0 {
		if rt := h.runtimes[h.order[0]]; rt != nil {
			first = rt.Server.Controller().WorkspaceRoot()
		}
	}
	h.mu.RUnlock()
	if first != "" {
		return first, nil
	}
	// Closing the last pane leaves nothing to infer from, and "open a session"
	// is exactly what someone does next. Fall back to the remembered list, the
	// same answer the window uses when it launches with no pane at all.
	for _, dir := range LaunchWorkspaces() {
		if st, err := os.Stat(dir); err == nil && st.IsDir() {
			return dir, nil
		}
	}
	return "", refusal(http.StatusConflict, "workspace.none", errors.New("no workspace to open in — add a folder first"), nil)
}
