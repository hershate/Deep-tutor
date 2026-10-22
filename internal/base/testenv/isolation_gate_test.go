package testenv

import (
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// guardedTrees are the package trees of the root module. The nested modules
// (desktop, sdk/go) carry their own test binaries and are walked by their own
// jobs, so a name there is not this gate's business.
var guardedTrees = []string{"internal", "cmd", "tools"}

// A test that states where the home is has to hold that statement against the
// machine it runs on. The current spelling wins the lookup, so a machine
// carrying DEEPTUTOR_HOME decides the outcome of a test that said
// REASONIX_HOME — and the test writes into the machine's own files. A package
// that names one of these variables therefore installs the guard.
func TestPackagesNamingAGuardedVariableInstallTheGuard(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	self := filepath.Join("internal", "base", "testenv")
	offenders := []string{}
	for _, tree := range guardedTrees {
		base := filepath.Join(root, tree)
		err := filepath.WalkDir(base, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if !entry.IsDir() {
				return nil
			}
			switch entry.Name() {
			case "node_modules", "testdata", "vendor":
				return filepath.SkipDir
			}
			rel, relErr := filepath.Rel(root, path)
			if relErr != nil {
				return relErr
			}
			rel = filepath.ToSlash(rel)
			if rel == self {
				return filepath.SkipDir
			}
			named, hasMain, guarded, err := inspectPackage(path)
			if err != nil {
				return err
			}
			if named && !(hasMain && guarded) {
				offenders = append(offenders, rel)
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	slices.Sort(offenders)
	for _, offender := range offenders {
		t.Errorf("%s names a home location variable but installs no isolation guard; "+
			"add a TestMain calling testenv.RunWithIsolatedUserState", offender)
	}
}

// inspectPackage reads one package directory and reports whether it names a
// guarded variable, declares a TestMain, and refers to the guard.
func inspectPackage(dir string) (named, hasMain, guarded bool, err error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false, false, false, err
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") {
			continue
		}
		raw, readErr := os.ReadFile(filepath.Join(dir, entry.Name()))
		if readErr != nil {
			return false, false, false, readErr
		}
		text := string(raw)
		for _, name := range guardedVars {
			if strings.Contains(text, `"`+name+`"`) {
				named = true
			}
		}
		if strings.HasSuffix(entry.Name(), "_test.go") && strings.Contains(text, "func TestMain(") {
			hasMain = true
		}
		if strings.Contains(text, "RunWithIsolatedUserState") || strings.Contains(text, "IsolateUserState") {
			guarded = true
		}
	}
	return named, hasMain, guarded, nil
}
