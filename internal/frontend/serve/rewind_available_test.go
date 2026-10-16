package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reasonix/internal/session/control"
	"reasonix/internal/state/checkpoint"
	"testing"
)

type undoReadPort struct {
	control.SessionAPI
	offer *checkpoint.RewindUndo
}

func (p *undoReadPort) AvailableUndo() (*checkpoint.RewindUndo, error) { return p.offer, nil }

func TestRewindAvailableUndoRoute(t *testing.T) {
	for _, offer := range []*checkpoint.RewindUndo{nil, {TransactionID: "saved", Turn: 2, Files: 3}} {
		s := &Server{ctrl: &undoReadPort{offer: offer}}
		w := httptest.NewRecorder()
		s.routes().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/rewind/undo", nil))
		if w.Code != http.StatusOK {
			t.Fatalf("status: %d, %s", w.Code, w.Body.String())
		}
		var got *checkpoint.RewindUndo
		if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
			t.Fatal(err)
		}
		if offer == nil {
			if got != nil {
				t.Fatalf("empty offer: %+v", got)
			}
			continue
		}
		if got == nil || *got != *offer {
			t.Fatalf("offer: %+v, want %+v", got, offer)
		}
	}
}
