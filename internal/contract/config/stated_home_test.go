package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
)

// TestAStatedHomeConfigIsWrittenAsAUserFile proves a config that belongs to a
// stated home is written as the user file it is.
//
// A user file is rewritten whole, at the keys the save changed. A project file
// is written as a slice, and that slice drops the sections the project never
// declared — for a user file that means the user's own MCP servers and skill
// choices disappear the next time they change an unrelated setting. Which file
// kind a path is therefore follows the home it belongs to, not the process.
func TestAStatedHomeConfigIsWrittenAsAUserFile(t *testing.T) {
	home := testenv.TempDir(t)
	roots := RootsForHome(home)
	path := roots.UserConfigPath()
	seed := "config_version = 12\n" +
		"[[plugins]]\nname = \"srv\"\ntype = \"stdio\"\ncommand = \"node\"\nargs = [\"srv.js\"]\n"
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := roots.EditConfigFile(path, func(c *Config) error {
		return c.SetAskTimeoutSeconds(120)
	}); err != nil {
		t.Fatalf("edit: %v", err)
	}

	reloaded, err := roots.LoadForEditReadOnlyStrict(path)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if len(reloaded.Plugins) != 1 || reloaded.Plugins[0].Name != "srv" {
		t.Fatalf("plugins = %+v, want the user's own MCP server still declared", reloaded.Plugins)
	}
	if got := reloaded.Agent.AskTimeoutSeconds.Seconds; got != 120 {
		t.Fatalf("ask timeout = %d, want 120", got)
	}
}

// TestTheTwoBindingsDisagreeAboutTheSameFile is the crux: the process's
// binding never named this home, so the file is not its user file, while the
// binding that did name it says it is. A judgement that could not tell them
// apart would write one user's settings under another's rules.
func TestTheTwoBindingsDisagreeAboutTheSameFile(t *testing.T) {
	home := testenv.TempDir(t)
	roots := RootsForHome(home)
	path := roots.UserConfigPath()

	if !roots.isUserConfigPath(path) {
		t.Fatal("the stated home's config is not recognised as that home's user file")
	}
	if processRoots().isUserConfigPath(path) {
		t.Fatal("the process claims a home it never named")
	}
}

// TestAStatedHomesDeclarationIsTheUsersOwn proves a declaration in a stated
// home's config is recorded as that home's, not as the project's.
//
// The source decides where a declaration may be edited and whether it is
// global or local. A per-user file read as a project file would make the
// user's own server editable through the project's own file — and, with two
// users in one process, would let one user's declaration be rewritten under
// another's name.
func TestAStatedHomesDeclarationIsTheUsersOwn(t *testing.T) {
	home := testenv.TempDir(t)
	project := testenv.TempDir(t)
	roots := RootsForHome(home)
	seed := "config_version = 12\n" +
		"[[plugins]]\nname = \"srv\"\ntype = \"stdio\"\ncommand = \"node\"\n"
	if err := os.WriteFile(roots.UserConfigPath(), []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}

	cfg, err := roots.LoadForRootReadOnly(project)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(cfg.Plugins) != 1 {
		t.Fatalf("plugins = %+v, want the user's own declaration", cfg.Plugins)
	}
	if cfg.Plugins[0].Source != MCPSourceUserConfig {
		t.Fatalf("declaration source = %q, want it recorded as the user's own", cfg.Plugins[0].Source)
	}
}

// TestAnEditLoadBringsInTheStatedHomesCredentials proves the credentials an
// edit-load brings into the process are the stated home's.
//
// The .env a home keeps is where that user's provider keys live. Loading the
// process's instead means a user's session resolves api_key_env against
// another user's keys — and finds whichever user loaded last.
func TestAnEditLoadBringsInTheStatedHomesCredentials(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("REASONIX_CREDENTIALS_STORE", "file")
	statedHome := testenv.TempDir(t)

	// Arm the variable so the process environment is restored afterwards no
	// matter what the loader does to it.
	t.Setenv("DT_STATED_HOME_KEY", "not-loaded")

	const providerTOML = "config_version = 12\n" +
		"[[providers]]\nname = \"p\"\nkind = \"openai\"\nmodel = \"m\"\napi_key_env = \"DT_STATED_HOME_KEY\"\n"
	if err := os.WriteFile(filepath.Join(statedHome, "config.toml"), []byte(providerTOML), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(statedHome, ".env"), []byte("DT_STATED_HOME_KEY=stated-secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(processHome, ".env"), []byte("DT_STATED_HOME_KEY=process-secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	roots := RootsForHome(statedHome)
	roots.LoadForEdit(roots.UserConfigPath())

	if got := os.Getenv("DT_STATED_HOME_KEY"); got != "stated-secret" {
		t.Fatalf("credential value = %q, want the stated home's", got)
	}
}

// TestTheStatedHomesProviderAccessCountsAsTheUsersOwn proves the user's own
// provider list is read as the user's.
//
// A project-only list may not narrow a list the user never declared: the
// merge treats an undeclared user list as "allow all" and a project list as a
// narrowing. Reading the per-user file as a project file would let a project
// silently narrow a user's provider list.
func TestTheStatedHomesProviderAccessCountsAsTheUsersOwn(t *testing.T) {
	home := testenv.TempDir(t)
	project := testenv.TempDir(t)
	roots := RootsForHome(home)
	if err := os.WriteFile(roots.UserConfigPath(),
		[]byte("config_version = 12\n[desktop]\nprovider_access = [\"mine\"]\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(project, "reasonix.toml"),
		[]byte("[desktop]\nprovider_access = [\"theirs\"]\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	cfg, err := roots.LoadForRootReadOnly(project)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if !strings.Contains(strings.Join(cfg.Desktop.ProviderAccess, ","), "mine") {
		t.Fatalf("provider access = %v, want the user's own entry kept", cfg.Desktop.ProviderAccess)
	}
}
