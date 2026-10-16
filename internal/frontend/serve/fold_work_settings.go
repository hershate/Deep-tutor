package serve

import (
	"encoding/json"
	"net/http"
)

func (s *Server) registerFoldWorkRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /fold-work-settings", s.foldWorkSettings)
	mux.HandleFunc("POST /fold-work-settings", s.saveFoldWorkSettings)
}

func (s *Server) foldWorkSettings(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, s.ctl().FoldWorkSettings())
}

func (s *Server) saveFoldWorkSettings(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Fold *bool `json:"fold"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10)).Decode(&body); err != nil || body.Fold == nil {
		badBody(w)
		return
	}
	if err := s.ctl().SaveFoldWorkSettings(*body.Fold); err != nil {
		saveFailed(w, http.StatusInternalServerError, "fold_work.save_failed", err)
		return
	}
	writeJSON(w, s.ctl().FoldWorkSettings())
}
