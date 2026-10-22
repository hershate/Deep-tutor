package testenv

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"deep-tutor/internal/base/scratchroot"
)

// under reports whether child sits inside parent.
func under(parent, child string) bool {
	rel, err := filepath.Rel(parent, child)
	if err != nil || filepath.IsAbs(rel) {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

func TestIsolateUserStateRedirectsAndRestoresCallerEnvironment(t *testing.T) {
	callerHome := t.TempDir()
	callerEnvironment := map[string]string{
		"HOME":                callerHome,
		"USERPROFILE":         callerHome,
		"XDG_CONFIG_HOME":     filepath.Join(callerHome, "caller-config"),
		"XDG_CACHE_HOME":      filepath.Join(callerHome, "caller-xdg-cache"),
		"XDG_STATE_HOME":      filepath.Join(callerHome, "caller-xdg-state"),
		"AppData":             filepath.Join(callerHome, "caller-appdata"),
		"LocalAppData":        filepath.Join(callerHome, "caller-local-appdata"),
		"REASONIX_HOME":       filepath.Join(callerHome, "explicit-reasonix-home"),
		"REASONIX_STATE_HOME": filepath.Join(callerHome, "caller-state"),
		"REASONIX_CACHE_HOME": filepath.Join(callerHome, "caller-cache"),
	}
	for key, value := range callerEnvironment {
		t.Setenv(key, value)
	}

	cleanup, err := IsolateUserState()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(cleanup)
	isolateHome := os.Getenv("HOME")
	if isolateHome == "" || isolateHome == callerHome {
		t.Fatalf("HOME = %q, want a disposable home distinct from caller %q", isolateHome, callerHome)
	}
	scratchRoot := scratchroot.Base()
	if rel, err := filepath.Rel(scratchRoot, isolateHome); err != nil || filepath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		t.Fatalf("isolated home %q is not contained by the scratch root %q", isolateHome, scratchRoot)
	}
	// The caller's home is the one directory a test must leave nothing
	// behind in: a removal that lost to a file handle used to leave a
	// directory there for every run.
	if rel, err := filepath.Rel(callerHome, isolateHome); err == nil && !filepath.IsAbs(rel) && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		t.Fatalf("isolated home %q is inside the caller's home %q", isolateHome, callerHome)
	}
	wantIsolatedEnvironment := map[string]string{
		"HOME":            isolateHome,
		"USERPROFILE":     isolateHome,
		"XDG_CONFIG_HOME": filepath.Join(isolateHome, ".config"),
		"XDG_CACHE_HOME":  filepath.Join(isolateHome, ".cache"),
		"XDG_STATE_HOME":  filepath.Join(isolateHome, ".local", "state"),
		"AppData":         filepath.Join(isolateHome, "AppData", "Roaming"),
		"LocalAppData":    filepath.Join(isolateHome, "AppData", "Local"),
	}
	for key, want := range wantIsolatedEnvironment {
		if got := os.Getenv(key); got != want {
			t.Errorf("%s inside isolated test process = %q, want %q", key, got, want)
		}
	}
	for _, key := range []string{"REASONIX_HOME", "REASONIX_STATE_HOME", "REASONIX_CACHE_HOME"} {
		if _, ok := os.LookupEnv(key); ok {
			t.Fatalf("%s remained set inside isolated test process", key)
		}
	}
	// The scratch root must not drift to the machine's system volume because
	// this guard rewrites %LOCALAPPDATA% — the system temp lives under it, and
	// a rewritten one used to read as "the temp was redirected".
	if scratchRoot := scratchroot.Base(); under(os.TempDir(), scratchRoot) {
		t.Fatalf("scratch root %q fell back into the system temp %q", scratchRoot, os.TempDir())
	}

	cleanup()
	for key, want := range callerEnvironment {
		if got := os.Getenv(key); got != want {
			t.Errorf("%s after cleanup = %q, want %q", key, got, want)
		}
	}
	if _, err := os.Stat(isolateHome); !os.IsNotExist(err) {
		t.Fatalf("isolated home still exists after cleanup: %v", err)
	}
}
