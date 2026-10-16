package serve

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/session/control"
)

func TestSurfaceAnswerHTTPRefusalRetryAndExpiry(t *testing.T) {
	prompts := make(chan event.Ask, 2)
	ctrl := control.New(control.Options{Sink: event.FuncSink(func(e event.Event) {
		if e.Kind == event.AskRequest {
			prompts <- e.Ask
		}
	})})
	t.Cleanup(ctrl.Close)
	ctrl.SetSessionPath(filepath.Join(testenv.TempDir(t), "surface.jsonl"))
	srv := httptest.NewServer(operatorHandler(New(ctrl, NewBroadcaster(), config.ServeConfig{})))
	t.Cleanup(srv.Close)
	spec, err := surfacespec.Parse([]byte(`{"spec_version":1,"body":[{"kind":"field","key":"count","label":"Count","field":{"type":"number","required":true}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := ctrl.Ask(ctx, []event.AskQuestion{{ID: "surface", Surface: spec}}); done <- err }()
	var ask event.Ask
	select {
	case ask = <-prompts:
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	for i, value := range []string{`{"count":"2"}`, `{"count":2}`, `{"count":2}`} {
		body, err := json.Marshal(map[string]any{"id": ask.ID, "answers": []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(value)}}})
		if err != nil {
			t.Fatal(err)
		}
		resp, err := http.Post(srv.URL+"/answer", "application/json", strings.NewReader(string(body)))
		if err != nil {
			t.Fatal(err)
		}
		want := []int{400, 204, 409}[i]
		if resp.StatusCode != want {
			t.Fatalf("response %d: %d", i, resp.StatusCode)
		}
		if i != 1 {
			var reason Reason
			if err := json.NewDecoder(resp.Body).Decode(&reason); err != nil {
				t.Fatal(err)
			}
			if reason.Code != []string{"surface.value_invalid", "", "surface.expired"}[i] {
				t.Fatal(reason)
			}
		}
		resp.Body.Close()
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
}
