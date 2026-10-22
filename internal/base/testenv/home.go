package testenv

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"deep-tutor/internal/base/scratchroot"
)

// TestingM is the subset of testing.M needed by RunWithIsolatedUserState.
type TestingM interface {
	Run() int
}

type savedEnvironment struct {
	value string
	set   bool
}

// guardedVars are the variables a machine may carry that would point a test at
// the machine's own files. Both spellings of each are listed: the current one
// wins the lookup, so a machine carrying it decides the outcome of a test that
// stated its own location.
var guardedVars = []string{
	"DEEPTUTOR_HOME",
	"DEEPTUTOR_STATE_HOME",
	"DEEPTUTOR_CACHE_HOME",
	"DEEPTUTOR_WORKSPACE_ROOT",
	"REASONIX_HOME",
	"REASONIX_STATE_HOME",
	"REASONIX_CACHE_HOME",
	"REASONIX_WORKSPACE_ROOT",
}

// IsolateUserState redirects default user-scoped Reasonix paths to a disposable
// home and clears inherited explicit path overrides. Tests may still override
// any of these variables for a focused scenario after this process-level guard
// is installed.
func IsolateUserState() (func(), error) {
	home, err := isolatedHomeDir()
	if err != nil {
		return nil, err
	}

	set := map[string]string{
		"HOME":            home,
		"USERPROFILE":     home,
		"XDG_CONFIG_HOME": filepath.Join(home, ".config"),
		"XDG_CACHE_HOME":  filepath.Join(home, ".cache"),
		"XDG_STATE_HOME":  filepath.Join(home, ".local", "state"),
		"AppData":         filepath.Join(home, "AppData", "Roaming"),
		"LocalAppData":    filepath.Join(home, "AppData", "Local"),
	}
	unset := guardedVars

	saved := make(map[string]savedEnvironment, len(set)+len(unset))
	remember := func(key string) {
		if _, ok := saved[key]; ok {
			return
		}
		value, ok := os.LookupEnv(key)
		saved[key] = savedEnvironment{value: value, set: ok}
	}
	restore := func() {
		for key, previous := range saved {
			if previous.set {
				_ = os.Setenv(key, previous.value)
			} else {
				_ = os.Unsetenv(key)
			}
		}
	}
	fail := func(cause error) (func(), error) {
		restore()
		_ = removeTree(home, removeAttempts)
		return nil, cause
	}

	for key, value := range set {
		remember(key)
		if err := os.Setenv(key, value); err != nil {
			return fail(fmt.Errorf("set %s for isolated test home: %w", key, err))
		}
	}
	for _, key := range unset {
		remember(key)
		if err := os.Unsetenv(key); err != nil {
			return fail(fmt.Errorf("unset %s for isolated test home: %w", key, err))
		}
	}

	var once sync.Once
	return func() {
		once.Do(func() {
			restore()
			_ = removeTree(home, removeAttempts)
		})
	}, nil
}

// isolatedHomeDir creates the disposable home the isolated state lives in.
//
// It sits under the project scratch area rather than under the caller's home
// for two reasons. The caller's home is the one directory a test must never
// leave anything behind in, and a removal that lost to a file handle used to
// leave a directory there for every run. The scratch area is disposable by
// contract, so a directory that survives a run there is visible to a sweep.
func isolatedHomeDir() (string, error) {
	root, err := scratchroot.Ensure()
	if err != nil {
		return "", fmt.Errorf("create isolated test home: %w", err)
	}
	home, err := os.MkdirTemp(root, ".reasonix-test-home-*")
	if err != nil {
		return "", fmt.Errorf("create isolated test home: %w", err)
	}
	return home, nil
}

// RunWithIsolatedUserState runs a package test binary behind the user-state
// isolation guard and exits with the test result.
func RunWithIsolatedUserState(m TestingM) {
	cleanup, err := IsolateUserState()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	exitCode := m.Run()
	cleanup()
	os.Exit(exitCode)
}
