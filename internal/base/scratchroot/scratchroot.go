// Package scratchroot resolves the directory the project keeps disposable
// scratch state in.
//
// Scratch state is disposable by definition: test temporaries, the
// session-private temporary directories and the runtime scratch trees all
// hold content that can be regenerated and that no user ever reads. What
// matters is where it lands.
//
// The system volume is the wrong place for it. It is small, the operating
// system reclaims it on its own schedule, and it holds every other tenant of
// the machine. A project that writes trees rather than files needs a volume
// that is sized for that.
//
// Resolution order:
//
//  1. The temporary directory the operating system reports, when it is one
//     the caller redirected. A harness that isolates through TMPDIR is
//     stating where its temporaries go, and nothing overrides that.
//  2. $DEEPTUTOR_SCRATCH_DIR, when it holds a path. This is the operator's
//     explicit choice for a deployment.
//  3. TEMP on the volume that holds the project, when that volume is not the
//     one the operating system keeps its own temporary files on. TEMP is the
//     conventional name for a scratch area, so the layout stays
//     recognizable; the project's own subdirectory keeps the area to itself.
//  4. The operating system's temporary directory.
//
// Base answers with a directory; callers create their own children under it.
package scratchroot

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
)

const (
	// EnvVar names the directory the project keeps scratch state in.
	EnvVar = "DEEPTUTOR_SCRATCH_DIR"
	// ProjectDirName is the project's own directory under a scratch area.
	ProjectDirName = "deeptutor"
	// volumeTempName is the conventional scratch area on a volume.
	volumeTempName = "TEMP"
)

// Seams: the resolution reads the environment, the running image and the
// filesystem, and a test has to be able to state all three.
var (
	envLookup         = os.Getenv
	executable        = os.Executable
	workingDir        = os.Getwd
	systemTemp        = os.TempDir
	systemDefaultTemp = defaultSystemTemp
	statPath          = os.Stat
	probeWrites       = probeWritable
)

var (
	probeMu    sync.Mutex
	probeCache = map[string]bool{}
)

// Base returns the directory scratch state belongs under. It does not create
// it.
//
// The answer is read from the environment every time, the way the operating
// system's own temporary directory is: a harness that redirects its temporary
// directory between runs expects the next call to follow, and a cached
// answer would keep sending it to the directory it just left.
func Base() string { return resolve() }

// Ensure returns the scratch directory and creates it when it is missing.
func Ensure() (string, error) { return ensureAt(Base()) }

// Path returns a named path under the scratch area. The scratch area itself
// is not created: a caller that owns the path creates the directory it needs.
func Path(name string) string { return filepath.Join(Base(), name) }

// ensureAt creates a directory the caller already chose.
func ensureAt(base string) (string, error) {
	if err := os.MkdirAll(base, 0o700); err != nil {
		return "", fmt.Errorf("scratchroot: create %s: %w", base, err)
	}
	return base, nil
}

// resolve answers where scratch state goes. It is separate from Base so a
// test can ask the question with its own environment.
func resolve() string {
	// A redirected temporary directory is the most specific statement
	// about where temporaries go: a harness that isolates through TMPDIR
	// has to keep isolating, or two runs share a directory.
	if redirectedSystemTemp() {
		return filepath.Clean(systemTemp())
	}
	if v := strings.TrimSpace(envLookup(EnvVar)); v != "" {
		if filepath.IsAbs(v) {
			return filepath.Clean(v)
		}
		// A relative override would land wherever the process happens to
		// start. Anchor it to the working directory so it at least stays
		// put for the life of the process.
		if abs, err := filepath.Abs(v); err == nil {
			return abs
		}
	}
	if base, ok := volumeTempBase(); ok {
		return filepath.Join(base, ProjectDirName)
	}
	return filepath.Join(systemTemp(), ProjectDirName)
}

// redirectedSystemTemp reports whether the temporary directory the operating
// system reports is one the caller chose rather than the system's own.
func redirectedSystemTemp() bool {
	def := systemDefaultTemp()
	if def == "" {
		return false
	}
	return !samePath(systemTemp(), def)
}

// defaultSystemTemp is where the operating system keeps its temporary files
// when nothing redirects it.
func defaultSystemTemp() string {
	if runtime.GOOS == "windows" {
		if dir := strings.TrimSpace(envLookup("LOCALAPPDATA")); dir != "" {
			return filepath.Join(dir, "Temp")
		}
		if dir := strings.TrimSpace(envLookup("SystemRoot")); dir != "" {
			return filepath.Join(dir, "Temp")
		}
		return ""
	}
	return filepath.Join(string(filepath.Separator), "tmp")
}

// samePath compares two paths the way the operating system does: cleaned,
// and without regard to case where the filesystem has none.
func samePath(a, b string) bool {
	ca, cb := filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "windows" {
		return strings.EqualFold(ca, cb)
	}
	return ca == cb
}

// volumeTempBase is the scratch area on the volume that holds the project.
// It answers false when that volume is the one the operating system keeps
// its temporary files on, because moving a directory from the system volume
// to the system volume is not a move.
func volumeTempBase() (string, bool) {
	vol := projectVolume()
	if vol == "" {
		return "", false
	}
	if vol == filepath.VolumeName(systemTemp()) {
		return "", false
	}
	candidate := filepath.Join(vol+string(filepath.Separator), volumeTempName)
	info, err := statPath(candidate)
	if err != nil || !info.IsDir() {
		return "", false
	}
	if !writable(candidate) {
		return "", false
	}
	return candidate, true
}

// projectVolume is the volume the project runs from: the running image's
// volume when it can be read, the working directory's otherwise.
func projectVolume() string {
	if exe, err := executable(); err == nil && exe != "" {
		if v := filepath.VolumeName(exe); v != "" {
			return v
		}
	}
	if wd, err := workingDir(); err == nil && wd != "" {
		return filepath.VolumeName(wd)
	}
	return ""
}

// writable reports whether a directory accepts a new file. The answer is
// cached per directory: the probe writes, and a process that asks often
// should not keep paying for it.
func writable(dir string) bool {
	probeMu.Lock()
	if ok, seen := probeCache[dir]; seen {
		probeMu.Unlock()
		return ok
	}
	probeMu.Unlock()

	ok := probeWrites(dir)

	probeMu.Lock()
	probeCache[dir] = ok
	probeMu.Unlock()
	return ok
}

// probeWritable writes and removes a throwaway file. A directory that cannot
// hold one is not a scratch area.
func probeWritable(dir string) bool {
	f, err := os.CreateTemp(dir, ".deeptutor-probe-*")
	if err != nil {
		return false
	}
	name := f.Name()
	_ = f.Close()
	if err := os.Remove(name); err != nil {
		// The probe file itself is the leftover now. Leaving it would
		// fill the area with the very files this package avoids.
		_ = os.Remove(name)
		return false
	}
	return true
}
