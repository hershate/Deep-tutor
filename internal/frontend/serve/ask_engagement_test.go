package serve

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/session/control"
)

func TestAskEngagementKeepsWaitingPastTheTimeout(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	t.Setenv("REASONIX_STATE_HOME", testenv.TempDir(t))
	asks := make(chan event.Ask, 1)
	ctrl := control.New(control.Options{Sink: event.FuncSink(func(e event.Event) {
		if e.Kind == event.AskRequest {
			asks <- e.Ask
		}
	})})
	t.Cleanup(ctrl.Close)
	ctrl.EnableInteractiveApproval()
	path := config.UserConfigPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("[agent]\nask_timeout_seconds = 10\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(operatorHandler(New(ctrl, NewBroadcaster(), config.ServeConfig{})))
	defer srv.Close()
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		ans, err := ctrl.Ask(ctx, []event.AskQuestion{{ID: "q", Prompt: "Which?"}})
		if err == nil && (len(ans) != 1 || len(ans[0].Selected) != 1 || ans[0].Selected[0] != "B") {
			err = context.Canceled
		}
		done <- err
	}()
	var ask event.Ask
	select {
	case ask = <-asks:
	case <-time.After(2 * time.Second):
		t.Fatal("question was not emitted")
	}
	body, _ := json.Marshal(map[string]any{"id": ask.ID, "engage": true})
	for range 2 {
		resp := postProvider(t, srv.URL, "/answer", string(body))
		resp.Body.Close()
		if resp.StatusCode != http.StatusNoContent {
			t.Fatalf("engage = %d", resp.StatusCode)
		}
	}
	select {
	case err := <-done:
		t.Fatalf("interaction settled the question: %v", err)
	case <-time.After(11 * time.Second):
	}
	ctrl.ReplayPendingPrompts()
	select {
	case replay := <-asks:
		if replay.ID != ask.ID {
			t.Fatal("replay changed the question")
		}
	default:
		t.Fatal("engaged question did not survive replay")
	}
	answer := postProvider(t, srv.URL, "/answer", `{"id":"`+ask.ID+`","answers":[{"QuestionID":"q","Selected":["B"]}]}`)
	answer.Body.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("engaged question stopped accepting answers")
	}
}

func TestAskEngagementRefusesStaleAndMixedRequests(t *testing.T) {
	ctrl := control.New(control.Options{})
	t.Cleanup(ctrl.Close)
	s := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	for _, tc := range []struct {
		body   string
		status int
	}{
		{`{"id":"stale","engage":true}`, http.StatusNotFound},
		{`{"id":"stale","engage":true,"answers":[]}`, http.StatusBadRequest},
		{`{"engage":true}`, http.StatusBadRequest},
	} {
		resp := postProvider(t, srv.URL, "/answer", tc.body)
		resp.Body.Close()
		if resp.StatusCode != tc.status {
			t.Fatalf("%s = %d, want %d", tc.body, resp.StatusCode, tc.status)
		}
	}
	req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/answer", strings.NewReader(`{"id":"stale","engage":true}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Fatalf("engagement without the launch token = %d", w.Code)
	}
}
