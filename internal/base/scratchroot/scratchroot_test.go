package scratchroot

import (
	"os"
	"path/filepath"
	"testing"
)

// restoreSeams puts the resolution's environment, image and filesystem
// seams back the way they were. The probe cache is cleared with them: it
// remembers what a directory accepted, and a test that states a different
// filesystem has to be answered from that filesystem.
func restoreSeams(t *testing.T) {
	t.Helper()
	env, exe, wd, tmp, def, stat, probe := envLookup, executable, workingDir, systemTemp, systemDefaultTemp, statPath, probeWrites
	t.Cleanup(func() {
		envLookup, executable, workingDir, systemTemp, systemDefaultTemp, statPath, probeWrites = env, exe, wd, tmp, def, stat, probe
		probeMu.Lock()
		probeCache = map[string]bool{}
		probeMu.Unlock()
	})
}

// notRedirected states a temporary directory the caller did not choose.
func notRedirected(dir string) {
	systemTemp = func() string { return dir }
	systemDefaultTemp = func() string { return dir }
}

func TestARedirectedTemporaryDirectoryIsHonoured(t *testing.T) {
	restoreSeams(t)
	custom := filepath.Join(string(filepath.Separator), "isolated", "tmp")
	systemTemp = func() string { return custom }
	systemDefaultTemp = func() string { return filepath.Join(string(filepath.Separator), "system", "tmp") }
	envLookup = func(string) string { return "" }

	if got := resolve(); got != filepath.Clean(custom) {
		t.Fatalf("resolve() = %q, want the redirected directory %q", got, custom)
	}
}

func TestARedirectedTemporaryDirectoryOutranksTheProjectPreference(t *testing.T) {
	restoreSeams(t)
	custom := filepath.Join(string(filepath.Separator), "isolated", "tmp")
	systemTemp = func() string { return custom }
	systemDefaultTemp = func() string { return filepath.Join(string(filepath.Separator), "system", "tmp") }
	envLookup = func(string) string { return filepath.Join(string(filepath.Separator), "var", "scratch") }
	executable = func() (string, error) { return filepath.Join(string(filepath.Separator), "elsewhere", "app"), nil }

	if got := resolve(); got != filepath.Clean(custom) {
		t.Fatalf("resolve() = %q, want the redirected directory %q", got, custom)
	}
}

func TestExplicitEnvironmentOutranksTheVolumePreference(t *testing.T) {
	restoreSeams(t)
	chosen := filepath.Join(t.TempDir(), "scratch")
	envLookup = func(string) string { return chosen }
	notRedirected(filepath.Join(string(filepath.Separator), "system", "tmp"))
	// The environment answer must not depend on where the process runs.
	executable = func() (string, error) { return filepath.Join(string(filepath.Separator), "elsewhere", "app"), nil }

	if got := resolve(); got != chosen {
		t.Fatalf("resolve() = %q, want the explicit override %q", got, chosen)
	}
}

func TestRelativeEnvironmentIsAnchoredToTheWorkingDirectory(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "scratch-here" }
	notRedirected(filepath.Join(string(filepath.Separator), "system", "tmp"))

	got := resolve()
	if !filepath.IsAbs(got) {
		t.Fatalf("resolve() = %q, want an absolute path", got)
	}
	if filepath.Base(got) != "scratch-here" {
		t.Fatalf("resolve() = %q, want it to end in the override", got)
	}
	// A relative override is anchored where the process started, not to
	// whatever the working directory happens to be later.
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(cwd, "scratch-here"); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}

func TestWithoutAVolumeTheSystemTemporaryDirectoryIsUsed(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return "", os.ErrNotExist }
	workingDir = func() (string, error) { return "", os.ErrNotExist }
	tmp := filepath.Join(string(filepath.Separator), "system", "tmp")
	notRedirected(tmp)

	want := filepath.Join(tmp, ProjectDirName)
	if got := resolve(); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}

func TestBaseFollowsTheEnvironmentBetweenCalls(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return "", os.ErrNotExist }
	workingDir = func() (string, error) { return "", os.ErrNotExist }
	notRedirected(filepath.Join(string(filepath.Separator), "system", "tmp"))

	first := Base()
	// A harness that redirects its temporary directory expects the next
	// call to follow it: a cached answer would keep sending it to the
	// directory it just left.
	moved := filepath.Join(string(filepath.Separator), "moved")
	systemTemp = func() string { return moved }
	if second := Base(); second != moved {
		t.Fatalf("Base() = %q, want the redirected directory %q", second, moved)
	}
	if first == moved {
		t.Fatalf("Base() answered %q for both directories", moved)
	}
}

func TestEnsureCreatesTheDirectory(t *testing.T) {
	root := filepath.Join(t.TempDir(), "scratch")
	got, err := ensureAt(root)
	if err != nil {
		t.Fatalf("ensureAt: %v", err)
	}
	info, err := os.Stat(got)
	if err != nil {
		t.Fatalf("stat scratch dir: %v", err)
	}
	if !info.IsDir() {
		t.Fatalf("%s is not a directory", got)
	}
}

func TestEnsureReportsADirectoryItCannotCreate(t *testing.T) {
	// A path whose parent is a file cannot become a directory.
	blocked := filepath.Join(t.TempDir(), "file")
	if err := os.WriteFile(blocked, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ensureAt(filepath.Join(blocked, "scratch")); err == nil {
		t.Fatal("ensureAt accepted a path it cannot create")
	}
}
