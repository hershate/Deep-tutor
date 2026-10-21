package config

import (
	"path/filepath"
	"strings"
	"testing"
)

// The product was renamed, and the variable that says where its home is went
// with it. Both spellings have to work: a machine that set the old one
// before the rename keeps the home it had — and keeps its data off the
// system volume — while a machine that sets the new one is in control of
// where its home is.
func TestHomeResolvesUnderBothSpellings(t *testing.T) {
	t.Run("the current name", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("DEEPTUTOR_HOME", home)
		t.Setenv("REASONIX_HOME", "")
		if got := IsolatedHomeDir(); got != home {
			t.Fatalf("IsolatedHomeDir = %q, want %q", got, home)
		}
		if got := RootDir(RootHome); got != home {
			t.Fatalf("RootDir(home) = %q, want %q", got, home)
		}
	})
	t.Run("the legacy name still works", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("DEEPTUTOR_HOME", "")
		t.Setenv("REASONIX_HOME", home)
		if got := IsolatedHomeDir(); got != home {
			t.Fatalf("IsolatedHomeDir = %q, want %q", got, home)
		}
		if got := RootDir(RootHome); got != home {
			t.Fatalf("RootDir(home) = %q, want %q", got, home)
		}
	})
}

// The current name wins when both are set. The other order would let a
// stale value overrule a deliberate one: a user who moved their home to
// another volume would find the old path back.
func TestTheCurrentSpellingWinsOverTheLegacyOne(t *testing.T) {
	current := t.TempDir()
	legacy := t.TempDir()
	t.Setenv("DEEPTUTOR_HOME", current)
	t.Setenv("REASONIX_HOME", legacy)
	if got := RootDir(RootHome); got != current {
		t.Fatalf("RootDir(home) = %q, want the current spelling %q", got, current)
	}
}

// The surface that names the variable pinning a root has to name the one
// that is actually set. Naming the other leaves the user changing a
// variable nothing reads.
func TestRootPinnedByNamesTheVariableThatIsSet(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DEEPTUTOR_STATE_HOME", "")
	t.Setenv("REASONIX_STATE_HOME", home)
	if got := RootPinnedBy(RootState); got != "REASONIX_STATE_HOME" {
		t.Fatalf("RootPinnedBy(state) = %q, want the variable that is set", got)
	}
	t.Setenv("DEEPTUTOR_STATE_HOME", home)
	if got := RootPinnedBy(RootState); got != "DEEPTUTOR_STATE_HOME" {
		t.Fatalf("RootPinnedBy(state) = %q, want the current spelling", got)
	}
}

// The state and cache roots move with the same rule as the home: a machine
// that redirected them before the rename keeps its redirection.
func TestStateAndCacheRootsResolveUnderBothSpellings(t *testing.T) {
	t.Run("legacy state home", func(t *testing.T) {
		state := t.TempDir()
		t.Setenv("DEEPTUTOR_STATE_HOME", "")
		t.Setenv("REASONIX_STATE_HOME", state)
		if got := IsolatedStateDir(); got != state {
			t.Fatalf("IsolatedStateDir = %q, want %q", got, state)
		}
	})
	t.Run("current cache home", func(t *testing.T) {
		cache := t.TempDir()
		t.Setenv("DEEPTUTOR_CACHE_HOME", cache)
		t.Setenv("REASONIX_CACHE_HOME", "")
		got := RootDir(RootCache)
		if !strings.EqualFold(filepath.Clean(got), filepath.Clean(cache)) {
			t.Fatalf("RootDir(cache) = %q, want %q", got, cache)
		}
	})
}

// A project .env must not be able to set either spelling: a project file
// that moved the home out from under the runtime would be a project file
// deciding where the runtime keeps its state.
func TestProjectDotEnvCannotSetEitherHomeVariable(t *testing.T) {
	for _, key := range []string{"DEEPTUTOR_HOME", "REASONIX_HOME", "deeptutor_home"} {
		if !isProjectDotEnvControlKey(key) {
			t.Errorf("isProjectDotEnvControlKey(%q) = false, want true", key)
		}
	}
}

// A credential's name is also the variable that can supply it, and the
// product names one of them itself. The key it is stored under does not
// change — renaming a key orphans what is stored under it — but the variable
// that overrides it follows the product's current name, and the old name
// still works.
func TestAccountTokenIsOverridableUnderEitherSpelling(t *testing.T) {
	const key = "REASONIX_ACCOUNT_TOKEN"
	t.Setenv("DEEPTUTOR_ACCOUNT_TOKEN", "current-name")
	t.Setenv(key, "")
	if got := ResolveCredential(key).Value; got != "current-name" {
		t.Fatalf("ResolveCredential(%q) = %q, want the current spelling", key, got)
	}
	t.Setenv("DEEPTUTOR_ACCOUNT_TOKEN", "")
	t.Setenv(key, "legacy-name")
	if got := ResolveCredential(key).Value; got != "legacy-name" {
		t.Fatalf("ResolveCredential(%q) = %q, want the legacy spelling", key, got)
	}
}
