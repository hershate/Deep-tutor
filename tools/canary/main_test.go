package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
)

// These tests name the home variables to check that the runner replaces them,
// so this package holds that statement against the machine it runs on: a
// machine carrying DEEPTUTOR_HOME must not decide what they see.
func TestMain(m *testing.M) {
	testenv.RunWithIsolatedUserState(m)
}

func TestParseRunArgsHandsTheTestArgumentsThrough(t *testing.T) {
	home, args, err := parseRunArgs([]string{"--home", "C:\\tmp\\h", "--", "./...", "-count=1"})
	if err != nil {
		t.Fatal(err)
	}
	if home != "C:\\tmp\\h" {
		t.Fatalf("home = %q", home)
	}
	want := []string{"./...", "-count=1"}
	if !slices.Equal(args, want) {
		t.Fatalf("args = %v, want %v", args, want)
	}
}

func TestParseRunArgsRefusesAFlagItCannotRead(t *testing.T) {
	if _, _, err := parseRunArgs([]string{"--home"}); err == nil {
		t.Fatal("a --home without a directory was accepted")
	}
	if _, _, err := parseRunArgs([]string{"--", "./..."}); err != nil {
		t.Fatalf("a run without --home was refused: %v", err)
	}
}

// The first entry of a name wins, so a second entry for a variable the
// machine already carries would leave the suite reading the machine's home.
func TestCanaryEnvReplacesTheLocationVariables(t *testing.T) {
	t.Setenv("DEEPTUTOR_HOME", "F:\\machine\\home")
	t.Setenv("REASONIX_HOME", "F:\\machine\\home")
	t.Setenv("DEEPTUTOR_CACHE_HOME", "F:\\machine\\cache")
	t.Setenv("SOME_OTHER_VAR", "kept")

	env := canaryEnv("F:\\canary\\home")
	counts := map[string]int{}
	for _, entry := range env {
		name, _, _ := strings.Cut(entry, "=")
		counts[name]++
	}
	for _, name := range homeVars {
		if counts[name] != 1 {
			t.Fatalf("%s appears %d times in the child environment", name, counts[name])
		}
	}
	for _, name := range redirectedVars {
		if counts[name] != 0 {
			t.Fatalf("%s still names a location in the child environment", name)
		}
	}
	if counts["SOME_OTHER_VAR"] != 1 {
		t.Fatal("an unrelated variable was dropped from the child environment")
	}
}

func TestCanaryEnvPointsBothSpellingsAtTheCanary(t *testing.T) {
	t.Setenv("DEEPTUTOR_HOME", "F:\\machine\\home")
	env := canaryEnv("F:\\canary\\home")
	for _, entry := range env {
		if strings.HasPrefix(entry, "DEEPTUTOR_HOME=") || strings.HasPrefix(entry, "REASONIX_HOME=") {
			if !strings.HasSuffix(entry, "=F:\\canary\\home") {
				t.Fatalf("%s does not name the canary", entry)
			}
		}
	}
}

func TestLeakedListsWhatIsLeftInTheHome(t *testing.T) {
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, "cache", "history-search"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "cache", "history-search", "v1.sqlite"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	left := leaked(home)
	want := []string{"cache/", "cache/history-search/", "cache/history-search/v1.sqlite"}
	if !slices.Equal(left, want) {
		t.Fatalf("leaked = %v, want %v", left, want)
	}
}

func TestLeakedIsEmptyForAnUntouchedHome(t *testing.T) {
	if left := leaked(t.TempDir()); len(left) != 0 {
		t.Fatalf("leaked = %v, want none", left)
	}
}

// A home that already holds files would report them as the suite's, and a
// home that is the one the product resolves would report the user's files.
func TestUsableHomeRefusesWhatItCannotJudge(t *testing.T) {
	occupied := t.TempDir()
	if err := os.WriteFile(filepath.Join(occupied, "config.toml"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := usableHome(occupied); err == nil {
		t.Fatal("a home that already holds files was accepted")
	}

	t.Setenv("DEEPTUTOR_HOME", occupied)
	if err := usableHome(occupied); err == nil {
		t.Fatal("the home the product resolves was accepted as a canary")
	}
}

func TestDispatchRefusesAnUnknownSubcommand(t *testing.T) {
	if err := dispatch([]string{"inspect"}); err == nil {
		t.Fatal("an unknown subcommand was accepted")
	}
	if err := dispatch(nil); err == nil {
		t.Fatal("no subcommand was accepted")
	}
}
