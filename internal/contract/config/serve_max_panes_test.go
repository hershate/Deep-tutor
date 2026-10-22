package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPaneLimitFallsBackToTheDefault(t *testing.T) {
	// A hand-written zero or less must not remove the ceiling: an unbounded
	// hub is the thing the cap exists to prevent.
	for _, unset := range []int{0, -1, -100} {
		if got := (ServeConfig{MaxPanes: unset}).PaneLimit(); got != DefaultMaxPanes {
			t.Fatalf("PaneLimit with %d = %d, want %d", unset, got, DefaultMaxPanes)
		}
	}
	if got := (ServeConfig{MaxPanes: 3}).PaneLimit(); got != 3 {
		t.Fatalf("PaneLimit with 3 = %d, want 3", got)
	}
}

func TestMaxPanesSurvivesASaveAndKeepsTheRestOfServe(t *testing.T) {
	isolateUserConfigHome(t)
	path := UserConfigPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("[serve]\nauth_mode = \"token\"\ntoken = \"keep-me\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := LoadForEdit(path)
	cfg.Serve.MaxPanes = 24
	if err := cfg.SaveTo(path); err != nil {
		t.Fatal(err)
	}
	back := LoadForEdit(path)
	if back.Serve.MaxPanes != 24 || back.Serve.Token != "keep-me" || back.Serve.AuthMode != "token" {
		t.Fatalf("after save [serve] = %+v, want max_panes 24 with the token kept", back.Serve)
	}
	if got := back.Serve.PaneLimit(); got != 24 {
		t.Fatalf("PaneLimit after save = %d, want 24", got)
	}
}

func TestProjectConfigCannotPinThePaneLimit(t *testing.T) {
	isolateUserConfigHome(t)
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "reasonix.toml"), []byte("[serve]\nmax_panes = 999\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := LoadForRootReadOnly(root)
	if err != nil {
		t.Fatal(err)
	}
	if got := cfg.Serve.PaneLimit(); got != DefaultMaxPanes {
		t.Fatalf("project reasonix.toml pinned the pane limit to %d", got)
	}
}
