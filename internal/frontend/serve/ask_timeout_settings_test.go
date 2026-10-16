package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func TestAskTimeoutIsOffByDefaultAndPersistsInTheUserFile(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	if got := readJSON[control.AskTimeoutSettings](t, srv.URL, "/ask-timeout"); got.Seconds != 0 {
		t.Fatalf("unset reads %+v, want off", got)
	}
	ok := postProvider(t, srv.URL, "/ask-timeout", `{"seconds":120}`)
	ok.Body.Close()
	if ok.StatusCode != http.StatusOK {
		t.Fatalf("save = %d", ok.StatusCode)
	}
	if got := config.LoadForEdit(config.UserConfigPath()).Agent.AskTimeoutSeconds.Seconds; got != 120 {
		t.Fatalf("file holds %d, want 120", got)
	}
}

func TestAskTimeoutRefusesWhatCannotWaitAndABodyWithoutTheChoice(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	bad := postProvider(t, srv.URL, "/ask-timeout", `{"seconds":3}`)
	var refusal struct {
		Code string `json:"code"`
	}
	_ = json.NewDecoder(bad.Body).Decode(&refusal)
	bad.Body.Close()
	if bad.StatusCode != http.StatusBadRequest || refusal.Code != "ask_timeout.out_of_range" {
		t.Fatalf("3 seconds = %d %q, want 400 ask_timeout.out_of_range", bad.StatusCode, refusal.Code)
	}
	empty := postProvider(t, srv.URL, "/ask-timeout", `{}`)
	empty.Body.Close()
	if empty.StatusCode != http.StatusBadRequest {
		t.Fatalf("empty body = %d, want 400: a missing choice must not read as off", empty.StatusCode)
	}
	if config.LoadForEdit(config.UserConfigPath()).Agent.AskTimeoutSeconds.Seconds != 0 {
		t.Fatal("a refused save reached the file")
	}
}
