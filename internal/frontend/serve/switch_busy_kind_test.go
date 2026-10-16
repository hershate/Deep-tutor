package serve

import (
	"encoding/json"
	"net/http"
	"testing"

	"reasonix/internal/session/control"
)

type idleWithJobs struct {
	control.SessionAPI
	jobs int
}

func (c idleWithJobs) RuntimeStatus() control.RuntimeStatus {
	return control.RuntimeStatus{BackgroundJobs: c.jobs}
}

type switchRefusal struct {
	Code   string         `json:"code"`
	Params map[string]any `json:"params"`
}

func switchRefusedAs(t *testing.T, wrap func(control.SessionAPI) control.SessionAPI) switchRefusal {
	t.Helper()
	srv := newRichProviderServerAs(t, wrap)
	resp := postProvider(t, srv.URL, "/model", `{"ref":"rich/beta"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusConflict {
		b, _ := readAllString(resp)
		t.Fatalf("POST /model = %d, want 409: %s", resp.StatusCode, b)
	}
	var got switchRefusal
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	return got
}

// A turn in flight refuses the switch with its own code; jobs left after the
// turn ended do not (see TestModelSwitchWithOnlyBackgroundJobsKeepsTheJobRunning).
func TestModelSwitchRefusalSaysTurnInFlight(t *testing.T) {
	got := switchRefusedAs(t, func(c control.SessionAPI) control.SessionAPI { return midTurn{c} })
	if got.Code != "busy.switch_model" {
		t.Fatalf("mid-turn code = %q, want busy.switch_model", got.Code)
	}
}

// A runtime that cannot hand its jobs over would cancel them when replaced, so
// the switch refuses rather than do that quietly.
func TestModelSwitchRefusesWhenJobsCannotBeHandedOver(t *testing.T) {
	got := switchRefusedAs(t, func(c control.SessionAPI) control.SessionAPI { return idleWithJobs{c, 2} })
	if got.Code != "busy.switch_model" {
		t.Fatalf("code = %q, want busy.switch_model", got.Code)
	}
}
