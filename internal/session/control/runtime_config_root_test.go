package control

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/contract/tool"
)

// projectProviderTOML declares one provider of its own. A project that names
// a provider the user config does not is what tells a session apart from the
// one it is running in.
func projectProviderTOML(name string) string {
	return "[[providers]]\n" +
		"name = \"" + name + "\"\n" +
		"kind = \"openai\"\n" +
		"base_url = \"http://127.0.0.1:1/v1\"\n" +
		"models = [\"" + name + "-model\"]\n" +
		"default = \"" + name + "-model\"\n"
}

func writeProjectProvider(t *testing.T, dir, name string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, "reasonix.toml"), []byte(projectProviderTOML(name)), 0o644); err != nil {
		t.Fatalf("write project config: %v", err)
	}
	// A provider a project declares runs only after it is approved for that
	// project. Trusting it is the point of the test: the session must read
	// the configuration the user trusted, and only that one.
	cfg, err := config.LoadForRootReadOnly(dir)
	if err != nil {
		t.Fatalf("load project config: %v", err)
	}
	if err := config.NewProjectProgramStore(cfg.Roots().Home()).Approve(dir, cfg.PendingProjectPrograms()...); err != nil {
		t.Fatalf("approve project provider: %v", err)
	}
}

// A server that hosts several projects starts in one folder, and a session
// opened on another must still read that folder's configuration. Reading the
// process directory instead would hand the session the configuration of
// whatever folder the host happened to boot from.
func TestRuntimeConfigReadsTheSessionWorkspaceNotTheProcessDirectory(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("REASONIX_HOME", filepath.Join(home, ".reasonix"))
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("AppData", filepath.Join(home, "AppData"))

	workspace := testenv.TempDir(t)
	writeProjectProvider(t, workspace, "workspace-provider")
	elsewhere := testenv.TempDir(t)
	writeProjectProvider(t, elsewhere, "process-directory-provider")
	t.Chdir(elsewhere)

	c := New(Options{Sink: event.Discard, Registry: tool.NewRegistry(), WorkspaceRoot: workspace})
	defer c.Close()

	list := c.modelListText()
	if !strings.Contains(list, "workspace-provider") {
		t.Fatalf("model list = %q, want the provider the workspace declares", list)
	}
	if strings.Contains(list, "process-directory-provider") {
		t.Fatalf("model list = %q, want no provider from the process directory", list)
	}
}

// The completion menu reads the configuration the same way, and says which
// folder it read: a menu that answers from another project's configuration
// offers models the turn would then refuse.
func TestCompletionDataReadsTheSessionWorkspaceAndNamesIt(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("REASONIX_HOME", filepath.Join(home, ".reasonix"))
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("AppData", filepath.Join(home, "AppData"))

	workspace := testenv.TempDir(t)
	writeProjectProvider(t, workspace, "workspace-provider")
	elsewhere := testenv.TempDir(t)
	writeProjectProvider(t, elsewhere, "process-directory-provider")
	t.Chdir(elsewhere)

	c := New(Options{Sink: event.Discard, Registry: tool.NewRegistry(), WorkspaceRoot: workspace})
	defer c.Close()

	data := c.CompletionData("en")
	if data.ConfigRoot != workspace {
		t.Fatalf("config root = %q, want the workspace %q", data.ConfigRoot, workspace)
	}
	if !slices.Contains(data.ModelRefs, "workspace-provider/workspace-provider-model") {
		t.Fatalf("model refs = %v, want the workspace's own model", data.ModelRefs)
	}
	if slices.Contains(data.ModelRefs, "process-directory-provider/process-directory-provider-model") {
		t.Fatalf("model refs = %v, want no model from the process directory", data.ModelRefs)
	}
}

// The effort ladder is built from the provider entry of the current model, so
// it too has to come from the folder the session is in.
func TestTheEffortMenuResolvesTheProviderFromTheNamedRoot(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("REASONIX_HOME", filepath.Join(home, ".reasonix"))
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, ".config"))
	t.Setenv("AppData", filepath.Join(home, "AppData"))

	workspace := testenv.TempDir(t)
	writeProjectProvider(t, workspace, "workspace-provider")
	elsewhere := testenv.TempDir(t)
	writeProjectProvider(t, elsewhere, "process-directory-provider")
	t.Chdir(elsewhere)

	roots := config.Roots{}
	d := ArgData{CurrentModel: "workspace-provider/workspace-provider-model", Roots: roots, ConfigRoot: workspace}
	entry := currentEffortEntry(d)
	if entry == nil || entry.Name != "workspace-provider" {
		t.Fatalf("entry = %+v, want the provider the workspace declares", entry)
	}
	d = ArgData{CurrentModel: "process-directory-provider/process-directory-provider-model", Roots: roots, ConfigRoot: elsewhere}
	entry = currentEffortEntry(d)
	if entry == nil || entry.Name != "process-directory-provider" {
		t.Fatalf("entry = %+v, want the provider the named root declares", entry)
	}
}
