package serve

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/session/control"
)

func (s *Server) surfaceActionState(w http.ResponseWriter, r *http.Request) {
	s.bindMu.Lock()
	defer s.bindMu.Unlock()
	state, err := s.ctl().SurfaceActionState(r.PathValue("id"))
	if err != nil {
		refuseSurfaceAnswer(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(state)
}

func (s *Server) surfaceAction(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, surfacespec.MaxSpecBytes+4096)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	var req control.SurfaceActionRequest
	if err := dec.Decode(&req); err != nil {
		refuseSurfaceAnswer(w, surfacespec.ErrValueInvalid)
		return
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		refuseSurfaceAnswer(w, surfacespec.ErrValueInvalid)
		return
	}
	s.bindMu.Lock()
	defer s.bindMu.Unlock()
	if s.refuseKeylessTurn(w) {
		return
	}
	if err := s.promoteSessionLease(); err != nil {
		sessionInUse(w, err)
		return
	}
	rec, err := s.ctl().SubmitSurfaceAction(req, viaOf(r))
	if err != nil {
		var specErr *surfacespec.Error
		if errors.As(err, &specErr) {
			refuseSurfaceAnswer(w, err)
		} else {
			writeInboxError(w, err)
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(rec)
}
