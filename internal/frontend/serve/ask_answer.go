package serve

import (
	"encoding/json"
	"errors"
	"net/http"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/surfacespec"
)

// answer responds to an ask_request.
func (s *Server) answer(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID      string            `json:"id"`
		Answers []event.AskAnswer `json:"answers"`
		Engage  bool              `json:"engage"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, surfacespec.MaxSpecBytes+4096)
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		var limit *http.MaxBytesError
		if errors.As(err, &limit) {
			refuseSurfaceAnswer(w, surfacespec.ErrLimitExceeded)
			return
		}
		badBody(w)
		return
	}
	if body.ID == "" {
		missingField(w, "id")
		return
	}
	if body.Engage {
		if body.Answers != nil {
			badBody(w)
			return
		}
		if !s.ctl().EngageQuestion(body.ID) {
			refuse(w, http.StatusNotFound, codeAskNotFound, errAskNotFound.Error(), nil)
			return
		}
	} else if err := answerAs(s.ctl(), r, body.ID, body.Answers); err != nil {
		refuseSurfaceAnswer(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
