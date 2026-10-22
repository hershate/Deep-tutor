package config

import (
	"os"
	"path/filepath"
	"testing"

	"deep-tutor/internal/base/testenv"
)

// TestRootsSecretsReadsTheBoundHomeOnly proves the credential-protection
// settings are read from the home the binding names, and that a setting the
// file does not declare keeps its built-in default.
func TestRootsSecretsReadsTheBoundHomeOnly(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)

	home := testenv.TempDir(t)
	body := "[secrets]\nprotect_sensitive_files = true\n"
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}

	got, err := RootsForHome(home).Secrets()
	if err != nil {
		t.Fatal(err)
	}
	if !got.ProtectSensitiveFiles {
		t.Fatalf("secrets = %+v, want the declared protection", got)
	}
	if !got.ProtectCredentialFiles {
		t.Fatalf("secrets = %+v, want an absent setting to keep its default", got)
	}

	process, err := ProcessSecrets()
	if err != nil {
		t.Fatal(err)
	}
	if process.ProtectSensitiveFiles {
		t.Fatalf("process secrets = %+v, want the process home not to gain the user's setting", process)
	}
}

// TestRootsSecretsKeepsTheDefaultWhenTheFileCannotBeRead proves a broken
// configuration does not turn a protection off: the caller gets the built-in
// default and the error, and the default protects credential files.
func TestRootsSecretsKeepsTheDefaultWhenTheFileCannotBeRead(t *testing.T) {
	home := testenv.TempDir(t)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("[broken\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := RootsForHome(home).Secrets()
	if err == nil {
		t.Fatal("a broken configuration was read as a valid one")
	}
	if !got.ProtectCredentialFiles {
		t.Fatalf("secrets = %+v, want credential files still protected", got)
	}
}
