package serve

import (
	"encoding/json"
	"net/http"
)

func (s *Server) registerChangedFilesRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /changed-files-settings", s.changedFilesSettings)
	mux.HandleFunc("POST /changed-files-settings", s.saveChangedFilesSettings)
}

func (s *Server) changedFilesSettings(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, s.ctl().ChangedFilesSettings())
}

func (s *Server) saveChangedFilesSettings(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Show *bool `json:"show"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10)).Decode(&body); err != nil || body.Show == nil {
		badBody(w)
		return
	}
	if err := s.ctl().SaveChangedFilesSettings(*body.Show); err != nil {
		saveFailed(w, http.StatusInternalServerError, "changed_files.save_failed", err)
		return
	}
	writeJSON(w, s.ctl().ChangedFilesSettings())
}
