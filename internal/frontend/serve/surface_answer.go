package serve

import (
	"errors"
	"net/http"
	"reasonix/internal/contract/surfacespec"
)

func refuseSurfaceAnswer(w http.ResponseWriter, err error) {
	var e *surfacespec.Error
	if errors.As(err, &e) {
		status := http.StatusBadRequest
		if errors.Is(err, surfacespec.ErrExpired) {
			status = http.StatusConflict
		}
		refuse(w, status, string(e.Code), "", map[string]any{"path": e.Path})
		return
	}
	writeErr(w, http.StatusInternalServerError, err)
}
