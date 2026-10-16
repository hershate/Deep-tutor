package serve

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

type currencySelectionGate struct {
	*control.Controller
	selected chan struct{}
	release  chan struct{}
}

func (c *currencySelectionGate) SaveDisplayCurrency(mode string) error {
	close(c.selected)
	<-c.release
	return c.Controller.SaveDisplayCurrency(mode)
}

func TestDisplayCurrencySaveConflictsWhenSelectedOwnerIsReplaced(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	path := config.UserConfigPath()
	original := "config_version = 12\n[billing]\ndisplay_currency = \"CNY\"\n"
	if err := os.WriteFile(path, []byte(original), 0600); err != nil {
		t.Fatal(err)
	}
	bc := NewBroadcaster()
	old := &currencySelectionGate{Controller: control.New(control.Options{Sink: bc}), selected: make(chan struct{}), release: make(chan struct{})}
	release := sync.OnceFunc(func() { close(old.release) })
	t.Cleanup(release)
	t.Cleanup(old.Close)
	replacement := control.New(control.Options{Sink: bc})
	t.Cleanup(replacement.Close)
	s := New(old, bc, config.ServeConfig{})
	s.buildController = func(context.Context, string) (*control.Controller, error) { return replacement, nil }
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "http://localhost/display-currency", strings.NewReader(`{"mode":"USD"}`))
	r.Header.Set("Content-Type", "application/json")
	done := make(chan struct{})
	go func() { operatorHandler(s).ServeHTTP(w, r); close(done) }()
	select {
	case <-old.selected:
	case <-done:
		t.Fatalf("request never selected the old owner: %d %s", w.Code, w.Body.String())
	}
	if err := s.switchModel(t.Context(), "next-model"); err != nil {
		t.Fatal(err)
	}
	release()
	<-done
	var refusal struct{ Code string }
	if err := json.Unmarshal(w.Body.Bytes(), &refusal); err != nil {
		t.Fatal(err)
	}
	if w.Code != http.StatusConflict || refusal.Code != "display_currency.runtime_changed" {
		t.Fatalf("retired save = %d %s, want 409 display_currency.runtime_changed", w.Code, w.Body.String())
	}
	if s.Controller() != replacement || bc.DisplayCurrency() != "CNY" {
		t.Fatal("retired request changed the current owner or currency")
	}
	if raw, err := os.ReadFile(path); err != nil || string(raw) != original {
		t.Fatalf("retired request changed persisted config: %q, %v", raw, err)
	}
}
