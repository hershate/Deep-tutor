package serve

import (
	"encoding/json"
	"net/http"
)

func (s *Server) registerReceiptRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /receipt-settings", s.receiptSettings)
	mux.HandleFunc("POST /receipt-settings", s.saveReceiptSettings)
}

func (s *Server) receiptSettings(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, s.ctl().ReceiptSettings())
}

func (s *Server) saveReceiptSettings(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Show *bool `json:"show"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10)).Decode(&body); err != nil || body.Show == nil {
		badBody(w)
		return
	}
	if err := s.ctl().SaveReceiptSettings(*body.Show); err != nil {
		saveFailed(w, http.StatusInternalServerError, "receipt.save_failed", err)
		return
	}
	writeJSON(w, s.ctl().ReceiptSettings())
}
