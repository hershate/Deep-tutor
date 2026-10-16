package tui

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestAskNavigationEngagesOnceWithoutAnswering(t *testing.T) {
	m, kernel := testModel(t)
	apply(m, askEvent())
	for _, key := range []string{"down", "down", "right"} {
		cmd, _ := m.answerAsk(m.tr.OpenPrompt(), key)
		run(m, cmd)
	}
	calls := strings.Join(kernel.seen(), "\n")
	if strings.Count(calls, `"engage":true`) != 1 || strings.Contains(calls, `"answers"`) || m.tr.OpenPrompt() == nil {
		t.Fatalf("navigation failed to preserve the question: %s", calls)
	}
}

func TestAskEngagementFailureCanRetry(t *testing.T) {
	m, _ := testModel(t)
	var count atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if count.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	m.client = &Client{HTTP: srv.Client(), Base: srv.URL}
	apply(m, askEvent())
	for range 2 {
		cmd, _ := m.answerAsk(m.tr.OpenPrompt(), "down")
		run(m, cmd)
	}
	if count.Load() != 2 || m.ask.engagement != askEngaged || m.tr.OpenPrompt() == nil {
		t.Fatal("failed engagement was not retryable")
	}
}
