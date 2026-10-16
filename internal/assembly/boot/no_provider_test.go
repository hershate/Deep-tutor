package boot

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

// A user config that lists no provider is a state the app can reach (every
// provider removed) and must be able to start from, to the screen that asks for
// a connection. Before, the kernel refused to build and the window never opened.
func TestEffectBuildStartsWithNoProviderAndRefusesATurnWithAnIdentity(t *testing.T) {
	home := isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	userDir := filepath.Join(home, "reasonix")
	if err := os.MkdirAll(userDir, 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, userDir, "config.toml", "providers = []\n")
	t.Setenv("REASONIX_HOME", userDir)
	approveWorkspace(t, dir)

	ctrl, err := Build(context.Background(), Options{Sink: event.Discard})
	if err != nil {
		t.Fatalf("Build with no provider: %v", err)
	}
	defer ctrl.Close()
	if !ctrl.NoProvider() {
		t.Fatal("the controller does not say it has no provider")
	}
	if err := ctrl.Run(context.Background(), "hello"); !errors.Is(err, provider.ErrNoProvider) {
		t.Fatalf("run error = %v, want one that is provider.ErrNoProvider", err)
	}
}

// An empty provider list must be written as one: an absent key reads as a fresh
// install and brings the built-in providers back, which is what removing the
// last provider used to undo.
func TestAnEmptyProviderListIsWrittenAsEmpty(t *testing.T) {
	out := config.RenderTOMLForScope(&config.Config{}, config.RenderScopeUser)
	if !strings.Contains(out, "providers = []") || strings.Contains(out, "unconfigured") {
		t.Fatalf("an emptied config rendered as:\n%s", out)
	}
}
