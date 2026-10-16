package serve

import "net/http"

// rewindAvailableUndo reads the session's currently retained undo offer.
func (s *Server) rewindAvailableUndo(w http.ResponseWriter, r *http.Request) {
	offer, err := s.ctl().AvailableUndo()
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, offer)
}
