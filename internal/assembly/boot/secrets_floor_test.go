package boot

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"deep-tutor/internal/base/secrets"
	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
)

// TestAPerUserBuildDoesNotLetTheUsersFileDecideTheProcessProtections proves the
// credential-protection layers come from the global configuration when the
// build serves one user of many. The layers are process-wide — the tool layer
// reads them from one place — so a user's own file must not decide them for
// every other user.
func TestAPerUserBuildDoesNotLetTheUsersFileDecideTheProcessProtections(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)

	userHome := testenv.TempDir(t)
	writeUserSecrets(t, userHome, "[secrets]\nfilter_subprocess_env = true\nprotect_sensitive_files = true\n")

	loaded := config.SecretsConfig{FilterSubprocessEnv: true, ProtectSensitiveFiles: true, ProtectCredentialFiles: true}
	got := effectiveSecrets(Options{}, config.RootsForHome(userHome), loaded)
	if got.FilterSubprocessEnv || got.ProtectSensitiveFiles {
		t.Fatalf("process secrets = %+v, want the global ones, not the user's", got)
	}
	if !got.ProtectCredentialFiles {
		t.Fatalf("process secrets = %+v, want credential files protected", got)
	}
}

// TestTheGlobalConfigurationDecidesTheProcessProtections proves the global
// file's setting is the one that holds, in both directions: what it turns on
// stays on, and what it leaves off the user's file cannot turn on.
func TestTheGlobalConfigurationDecidesTheProcessProtections(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)
	writeUserSecrets(t, processHome, "[secrets]\nprotect_sensitive_files = true\n")

	userHome := testenv.TempDir(t)
	writeUserSecrets(t, userHome, "[secrets]\nprotect_sensitive_files = false\nfilter_subprocess_env = true\n")

	loaded := config.SecretsConfig{FilterSubprocessEnv: true, ProtectSensitiveFiles: false, ProtectCredentialFiles: true}
	got := effectiveSecrets(Options{}, config.RootsForHome(userHome), loaded)
	if !got.ProtectSensitiveFiles {
		t.Fatalf("process secrets = %+v, want the global protection to hold", got)
	}
	if got.FilterSubprocessEnv {
		t.Fatalf("process secrets = %+v, want the user's own setting not to reach the process", got)
	}
}

// TestTheStatedFloorDecidesTheProcessProtections proves a host that states the
// floor itself wins over both the global file and the user's own.
func TestTheStatedFloorDecidesTheProcessProtections(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)

	floor := config.SecretsConfig{FilterSubprocessEnv: true, ProtectSensitiveFiles: true, ProtectCredentialFiles: true}
	loaded := config.SecretsConfig{}
	got := effectiveSecrets(Options{GlobalSecrets: &floor}, config.RootsForHome(testenv.TempDir(t)), loaded)
	if got != floor {
		t.Fatalf("process secrets = %+v, want the stated floor %+v", got, floor)
	}
}

// TestASingleUserBuildKeepsItsOwnConfiguration proves a build that serves the
// process's own home keeps deciding for itself: there is no second user to
// protect the settings from.
func TestASingleUserBuildKeepsItsOwnConfiguration(t *testing.T) {
	loaded := config.SecretsConfig{FilterSubprocessEnv: true, ProtectSensitiveFiles: true}
	got := effectiveSecrets(Options{}, config.Roots{}, loaded)
	if got != loaded {
		t.Fatalf("process secrets = %+v, want the loaded ones %+v", got, loaded)
	}
}

// TestTheBuildArmsTheGlobalProtectionsForAPerUserSession proves the floor
// reaches the tool layer, not only the configuration: the tools read the
// package state, so a floor that stopped there would protect nothing.
func TestTheBuildArmsTheGlobalProtectionsForAPerUserSession(t *testing.T) {
	prevSensitive, prevCredential := secrets.ProtectSensitiveFiles(), secrets.ProtectCredentialFiles()
	t.Cleanup(func() {
		secrets.SetProtectSensitiveFiles(prevSensitive)
		secrets.SetProtectCredentialFiles(prevCredential)
	})

	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)
	writeUserSecrets(t, processHome, "[secrets]\nprotect_sensitive_files = true\n")

	userHome := testenv.TempDir(t)
	writeUserSecrets(t, userHome, "[secrets]\nprotect_sensitive_files = false\n")

	ctrl, err := Build(context.Background(), Options{
		Home:          userHome,
		WorkspaceRoot: testenv.TempDir(t),
		Sink:          event.Discard,
	})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()

	if !secrets.ProtectSensitiveFiles() {
		t.Fatal("the tool layer did not receive the global protection")
	}
}

func writeUserSecrets(t *testing.T, home, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}
