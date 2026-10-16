package serve

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
	"reasonix/internal/state/stats"
)

func fallbackTitleHub(t *testing.T) *Hub {
	t.Helper()
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	t.Setenv("REASONIX_STATE_HOME", home)
	t.Chdir(testenv.TempDir(t))
	kind := "fallback-title-" + filepath.Base(testenv.TempDir(t))
	provider.Register(kind, func(provider.Config) (provider.Provider, error) { return titleUsageProvider{}, nil })
	path := config.UserConfigPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	body := `default_model = "local/title-test"

[[providers]]
name = "local"
kind = "` + kind + `"
base_url = "http://127.0.0.1:1/v1"
models = ["title-test"]
default = "title-test"
`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := stats.CloseUsageCatalogs(ctx); err != nil {
			t.Error(err)
		}
	})
	return NewHub(HubOptions{})
}

func TestSessionTitleFallbackReusesProviderAndUsageSink(t *testing.T) {
	h := fallbackTitleHub(t)
	first := h.sessionTitleGenerator(testenv.TempDir(t))
	if first.titleProv == nil || first.titleUsageSink == nil {
		t.Fatal("fallback needs a provider and a usage recorder")
	}
	if err := os.WriteFile(config.UserConfigPath(), []byte("broken = ["), 0o600); err != nil {
		t.Fatal(err)
	}
	second := h.sessionTitleGenerator(testenv.TempDir(t))
	if second != first || second.titleUsageSink != first.titleUsageSink {
		t.Fatal("fallback reloaded config or replaced its provider/usage recorder")
	}
}

func TestAutoNameFallbackRecordsUsageWithoutRuntime(t *testing.T) {
	h := fallbackTitleHub(t)
	root := testenv.TempDir(t)
	rememberWorkspace(root)
	path := filepath.Join(SessionDirFor(root), "20261009-120000.jsonl")
	session := sessionstore.NewSession("system")
	session.Add(provider.Message{Role: provider.RoleUser, Content: "name this conversation"})
	if err := session.SaveSnapshot(path); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		res := titlePost(t, h, "auto-name", path, "")
		if res.Code != http.StatusOK || !strings.Contains(res.Body.String(), "Short title") {
			t.Fatalf("fallback naming: %d %s", res.Code, res.Body.String())
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := stats.Flush(ctx, config.StatsDir()); err != nil {
		t.Fatal(err)
	}
	files, err := filepath.Glob(filepath.Join(config.StatsDir(), "*.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	calls, tokens := 0, 0
	for _, file := range files {
		raw, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		for line := range strings.SplitSeq(strings.TrimSpace(string(raw)), "\n") {
			var record struct {
				Model       string `json:"model"`
				UsageSource string `json:"usage_source"`
				Total       int    `json:"total"`
			}
			if err := json.Unmarshal([]byte(line), &record); err != nil {
				t.Fatal(err)
			}
			if record.UsageSource == event.UsageSourceTitle && record.Model == "local/title-test" {
				calls++
				tokens += record.Total
			}
		}
	}
	if calls != 2 || tokens != 24 {
		t.Fatalf("fallback usage: %d calls, %d tokens; want 2 calls and 24 tokens", calls, tokens)
	}
	if len(h.List()) != 0 {
		t.Fatal("fallback naming opened a runtime")
	}
}

func TestSessionTitleFallbackRecoversAfterInitializationFailure(t *testing.T) {
	h := fallbackTitleHub(t)
	path := config.UserConfigPath()
	good, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("broken = ["), 0o600); err != nil {
		t.Fatal(err)
	}
	first := h.sessionTitleGenerator(testenv.TempDir(t))
	if first.titleProv != nil {
		t.Fatal("broken config unexpectedly created a title provider")
	}
	if err := os.WriteFile(path, good, 0o600); err != nil {
		t.Fatal(err)
	}
	second := h.sessionTitleGenerator(testenv.TempDir(t))
	if second.titleProv == nil || second.titleUsageSink == nil {
		t.Fatal("fallback did not recover after the configuration was repaired")
	}
	if got := second.generateTitle(t.Context(), "hello"); got != "Short title" {
		t.Fatalf("recovered title = %q", got)
	}
}
