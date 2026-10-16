package config

import (
	"slices"
	"strings"
	"testing"
)

func TestHostVerificationIsOnUnlessTheUserTurnedItOff(t *testing.T) {
	for value, want := range map[string]bool{"": false, "on": false, "off": true, "Off": true, " off ": true, "false": false, "nonsense": false} {
		cfg := Default()
		cfg.Agent.HostVerification = value
		if got := cfg.HostVerificationOff(); got != want {
			t.Errorf("host_verification = %q -> off %v, want %v", value, got, want)
		}
	}
}

func TestUserFileTurnsHostVerificationOff(t *testing.T) {
	cfg, _ := loadScoped(t, "[agent]\nhost_verification = \"off\"\n", "")
	if !cfg.HostVerificationOff() {
		t.Fatalf("a user file's off was not honoured: %q", cfg.Agent.HostVerification)
	}
}

func TestProjectCannotTurnHostVerificationOff(t *testing.T) {
	cfg, _ := loadScoped(t, "", "[agent]\nhost_verification = \"off\"\n")
	if cfg.HostVerificationOff() {
		t.Fatal("a cloned repo's reasonix.toml switched off the checks the host asks of a turn")
	}
	if !slices.Contains(ignoredKeys(cfg), "agent.host_verification") {
		t.Fatalf("ignored = %v, want agent.host_verification reported", ignoredKeys(cfg))
	}
}

func TestProjectCannotTurnBackOnWhatTheUserTurnedOff(t *testing.T) {
	cfg, _ := loadScoped(t, "[agent]\nhost_verification = \"off\"\n", "[agent]\nhost_verification = \"on\"\n")
	if !cfg.HostVerificationOff() {
		t.Fatal("the project file overrode the user's own host_verification")
	}
}

func TestHostVerificationOffSurvivesASave(t *testing.T) {
	cfg := Default()
	cfg.Agent.HostVerification = "off"
	if out := RenderTOMLForScope(cfg, RenderScopeUser); !strings.Contains(out, "host_verification = \"off\"") {
		t.Fatalf("rendered config lost the choice:\n%s", out)
	}
	if out := RenderTOMLForScope(Default(), RenderScopeUser); strings.Contains(out, "\nhost_verification = ") {
		t.Fatalf("the default was written as an active key:\n%s", out)
	}
}
