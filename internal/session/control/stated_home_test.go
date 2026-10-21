package control

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
)

// isolateProcessHome points the environment's home at a scratch directory and
// clears the working directory, so a test can tell the process's home from the
// home a session states.
func isolateProcessHome(t *testing.T) string {
	t.Helper()
	home := testenv.TempDir(t)
	t.Setenv("DEEPTUTOR_HOME", home)
	t.Setenv("REASONIX_HOME", home)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("AppData", filepath.Join(home, "AppData"))
	t.Chdir(testenv.TempDir(t))
	return home
}

// TestAStatedHomeIsWhereTheSessionsSettingsLive proves the controller reads and
// writes the user configuration of the home its assembly stated, not the one
// the process environment names. Two users served from one process must not
// edit each other's settings, and the file that answers "where did my setting
// go" must be the user's own.
func TestAStatedHomeIsWhereTheSessionsSettingsLive(t *testing.T) {
	processHome := isolateProcessHome(t)
	statedHome := testenv.TempDir(t)

	c := &Controller{controllerDeps: controllerDeps{roots: config.RootsForHome(statedHome)}}

	if err := c.SaveAskTimeoutSettings(42); err != nil {
		t.Fatalf("save ask timeout: %v", err)
	}

	stated := filepath.Join(statedHome, "config.toml")
	raw, err := os.ReadFile(stated)
	if err != nil {
		t.Fatalf("read stated home config: %v", err)
	}
	if !strings.Contains(string(raw), "ask_timeout_seconds = 42") {
		t.Fatalf("stated home config = %q, want the ask timeout written there", raw)
	}

	// The process's own home must not have gained the setting: a session that
	// states a home writes to that home only.
	if raw, err := os.ReadFile(filepath.Join(processHome, "config.toml")); err == nil {
		if strings.Contains(string(raw), "ask_timeout_seconds") {
			t.Fatalf("process home config = %q, want the process home untouched", raw)
		}
	}

	// And reading it back names the stated home's file, not the process's.
	if got := c.AskTimeoutSettings().Path; got != stated {
		t.Fatalf("ask timeout path = %q, want %q", got, stated)
	}
	if got := c.AskTimeoutSettings().Seconds; got != 42 {
		t.Fatalf("ask timeout seconds = %d, want 42", got)
	}
}

// TestTwoSessionsInOneProcessDoNotShareTheirSettings is the multi-user shape
// of the same fact: two controllers, two homes, one process. Neither may see
// or overwrite the other's settings.
func TestTwoSessionsInOneProcessDoNotShareTheirSettings(t *testing.T) {
	isolateProcessHome(t)
	homeA := testenv.TempDir(t)
	homeB := testenv.TempDir(t)

	a := &Controller{controllerDeps: controllerDeps{roots: config.RootsForHome(homeA)}}
	b := &Controller{controllerDeps: controllerDeps{roots: config.RootsForHome(homeB)}}

	if err := a.SaveAskTimeoutSettings(11); err != nil {
		t.Fatalf("save A: %v", err)
	}
	if err := b.SaveAskTimeoutSettings(22); err != nil {
		t.Fatalf("save B: %v", err)
	}

	if got := a.AskTimeoutSettings().Seconds; got != 11 {
		t.Fatalf("A reads %d, want 11", got)
	}
	if got := b.AskTimeoutSettings().Seconds; got != 22 {
		t.Fatalf("B reads %d, want 22", got)
	}
}

// TestAnMCPServerIsDeclaredInTheGlobalLibrary proves an MCP declaration lands
// in the global asset library, not in the session's own home. MCP servers are
// global admin-managed assets (note/12 #55): the admin installs them once and
// everybody may use them. A declaration written into one user's home would
// make the admin's server visible to that user and to nobody else.
func TestAnMCPServerIsDeclaredInTheGlobalLibrary(t *testing.T) {
	processHome := isolateProcessHome(t)
	statedHome := testenv.TempDir(t)
	workspace := testenv.TempDir(t)

	c := &Controller{controllerDeps: controllerDeps{
		roots:         config.RootsForHome(statedHome),
		workspaceRoot: workspace,
	}}

	entry := config.PluginEntry{Name: "srv", Type: "stdio", Command: "node"}
	if err := c.persistMCPServer(entry); err != nil {
		t.Fatalf("persist: %v", err)
	}

	raw, err := os.ReadFile(filepath.Join(processHome, "config.toml"))
	if err != nil {
		t.Fatalf("read global config: %v", err)
	}
	if !strings.Contains(string(raw), "[[plugins]]") || !strings.Contains(string(raw), `"srv"`) {
		t.Fatalf("global config = %q, want the server declared there", truncate(raw))
	}

	// The session's own home must not have gained the declaration.
	if raw, err := os.ReadFile(filepath.Join(statedHome, "config.toml")); err == nil {
		if strings.Contains(string(raw), "srv") {
			t.Fatalf("stated home config = %q, want the user's home untouched", truncate(raw))
		}
	}
}

// TestTheTrustDecisionAndTheRememberedRulesLiveInOneStore proves a folder's
// trust and the rules remembered for it are read from one place. They are two
// views of one record. Read from two homes, the rule the person granted is not
// the rule the session shows — and in a multi-user service the two homes are
// two different users' files.
func TestTheTrustDecisionAndTheRememberedRulesLiveInOneStore(t *testing.T) {
	isolateProcessHome(t)
	home := testenv.TempDir(t)
	other := testenv.TempDir(t)
	workspace := testenv.TempDir(t)

	// The posture names a different home on purpose: a host may fill in what it
	// likes, but the session's own binding is where its records live.
	c := &Controller{controllerDeps: controllerDeps{
		roots:         config.RootsForHome(home),
		workspaceRoot: workspace,
		posture:       PostureEvidence{WritesConfined: true, Home: other},
	}}

	if err := c.SetWorkspaceTrust(config.WorkspaceTrusted); err != nil {
		t.Fatalf("decide trust: %v", err)
	}
	if got := c.WorkspaceTrust(); got != config.WorkspaceTrusted {
		t.Fatalf("trust = %q, want the decision to stick", got)
	}
	remembered := c.PermissionRules().RememberedPath
	if remembered == "" || !strings.Contains(remembered, home) {
		t.Fatalf("remembered path = %q, want it under the session's own home %q", remembered, home)
	}
	if strings.Contains(remembered, other) {
		t.Fatalf("remembered path = %q, want it under %q, not the posture's %q", remembered, home, other)
	}
}

// truncate keeps a failure message about a whole config file readable.
func truncate(raw []byte) string {
	const limit = 400
	if len(raw) <= limit {
		return string(raw)
	}
	return string(raw[:limit]) + "..."
}
