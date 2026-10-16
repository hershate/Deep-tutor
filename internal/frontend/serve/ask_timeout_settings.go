package serve

import (
	"encoding/json"
	"errors"
	"net/http"

	"reasonix/internal/contract/config"
)

func (s *Server) registerAskTimeoutRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /ask-timeout", s.askTimeoutSettings)
	mux.HandleFunc("POST /ask-timeout", s.saveAskTimeoutSettings)
}

func (s *Server) askTimeoutSettings(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, s.ctl().AskTimeoutSettings())
}

// saveAskTimeoutSettings needs no rebuild: each question reads the setting as
// it starts waiting. A body without the choice is refused, never read as off.
func (s *Server) saveAskTimeoutSettings(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Seconds *int `json:"seconds"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10)).Decode(&body); err != nil || body.Seconds == nil {
		badBody(w)
		return
	}
	if err := s.ctl().SaveAskTimeoutSettings(*body.Seconds); err != nil {
		if errors.Is(err, config.ErrAskTimeoutOutOfRange) {
			saveFailed(w, http.StatusBadRequest, "ask_timeout.out_of_range", err)
			return
		}
		saveFailed(w, http.StatusInternalServerError, "ask_timeout.save_failed", err)
		return
	}
	writeJSON(w, s.ctl().AskTimeoutSettings())
}
