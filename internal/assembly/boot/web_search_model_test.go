package boot

import (
	"path/filepath"
	"testing"

	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/config"
)

// NewWebSearchModel is the one construction both the registry's web_search
// tool and the tutor surface resolve through: the routed provider plus the
// ref its spend prices under, Reasonix's own reasons when the selection is
// broken, and empty when no selection is set.
func TestNewWebSearchModelResolvesSelection(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	writeFile(t, filepath.Dir(config.UserConfigPath()), "config.toml", `
[[providers]]
name = "search"
kind = "anthropic"
base_url = "https://fixture.invalid"
model = "finder"
api_key = "fixture"
web_search = true

[agent]
web_search_model = "search/finder"
`)
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	p, ref, err := NewWebSearchModel(cfg, netclient.ProxySpec{Mode: netclient.ModeOff})
	if err != nil {
		t.Fatalf("NewWebSearchModel: %v", err)
	}
	if p == nil || ref != "search/finder" {
		t.Fatalf("selection = %v, %q", p, ref)
	}

	cfg.Agent.WebSearchModel = ""
	p, ref, err = NewWebSearchModel(cfg, netclient.ProxySpec{Mode: netclient.ModeOff})
	if err != nil || p != nil || ref != "" {
		t.Fatalf("unset selection = %v, %q, %v", p, ref, err)
	}

	cfg.Agent.WebSearchModel = "ghost/none"
	_, ref, err = NewWebSearchModel(cfg, netclient.ProxySpec{Mode: netclient.ModeOff})
	if err == nil || ref != "" {
		t.Fatalf("broken selection = %q, %v", ref, err)
	}
	if reason, ok := config.WebSearchReasonOf(err); !ok || reason != config.WebSearchModelRemoved {
		t.Fatalf("reason = %q, %v", reason, err)
	}
}
