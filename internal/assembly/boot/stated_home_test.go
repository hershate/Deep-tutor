package boot

import (
	"context"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/event"
)

// TestARebuildInheritsTheStatedHome proves a replacement runtime keeps the
// binding of the controller it replaces: a model switch rebuilds the session,
// it is not a new session that may pick another home. The home named here
// carries its own provider declaration, so the build can only succeed if it
// read the configuration of the home it was given.
func TestARebuildInheritsTheStatedHome(t *testing.T) {
	home := statedBootHome(t)
	workspace := testenv.TempDir(t)

	writeFile(t, home, "config.toml", `
default_model = "test-model/test-model"

[[providers]]
name = "test-model"
kind = "openai"
base_url = "https://example.invalid/v1"
model = "test-model"
api_key_env = "DEEPTUTOR_TEST_KEY_UNSET"
`)

	old, err := Build(context.Background(), Options{
		Model:         "test-model/test-model",
		Home:          home,
		WorkspaceRoot: workspace,
		Sink:          event.Discard,
	})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer old.Close()

	if got := old.Roots().StatedHome(); got != home {
		t.Fatalf("built controller stated home = %q, want %q", got, home)
	}

	// The rebuild states no home of its own — the way a frontend that rebuilds
	// from the live controller does.
	res, err := Rebuild(context.Background(), old, Options{WorkspaceRoot: workspace, Sink: event.Discard})
	if err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	defer res.Controller.Close()

	if got := res.Controller.Roots().StatedHome(); got != home {
		t.Fatalf("rebuilt controller stated home = %q, want %q", got, home)
	}
}
