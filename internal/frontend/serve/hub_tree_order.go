package serve

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

func (h *Hub) moveWorkspace(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Path      string  `json:"path"`
		Direction int     `json:"direction"`
		Before    *string `json:"before"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		badBody(w)
		return
	}
	dir := strings.TrimSpace(body.Path)
	if dir == "" {
		missingField(w, "path")
		return
	}
	var err error
	if body.Before != nil {
		// A drop names its place; a step names a direction. Not both.
		err = placeWorkspace(r.Context(), dir, strings.TrimSpace(*body.Before))
	} else if body.Direction != -1 && body.Direction != 1 {
		badValue(w, "direction", "-1", "1")
		return
	} else {
		err = moveWorkspace(r.Context(), dir, body.Direction)
	}
	if errors.Is(err, errWorkspaceNotRemembered) {
		notFound(w, "workspace", dir)
		return
	}
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
