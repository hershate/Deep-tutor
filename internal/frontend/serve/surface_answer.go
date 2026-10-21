package serve

import (
	"deep-tutor/internal/contract/surfacespec"
	"errors"
	"net/http"
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
