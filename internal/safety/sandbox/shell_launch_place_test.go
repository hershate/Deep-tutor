package sandbox

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestShellLaunchPlace(t *testing.T) {
	for p, want := range map[string]bool{
		`C:\Git`:          true,
		`d:/Tools`:        true,
		`\\srv\share\Git`: false,
		`//srv/share/Git`: false,
		`\\?\UNC\srv\x`:   false,
		`\\.\pipe\x`:      false,
		`\??\UNC\srv\x`:   false,
		`Git`:             false,
		`C:Git`:           false,
		``:                false,
	} {
		if got := shellLaunchPlace(true, p); got != want {
			t.Errorf("shellLaunchPlace(%q) = %v, want %v", p, got, want)
		}
	}
}

// One hostile root, fed to every source of bash candidates and to the pinned
// path, never reaches a candidate list or a launch.
func TestEverySourceRefusesANetworkShareRoot(t *testing.T) {
	old := windowsPaths
	windowsPaths = true
	t.Cleanup(func() { windowsPaths = old })
	const share = `\\srv\share`
	env := map[string]string{"ProgramFiles": share, "ProgramW6432": share, "LOCALAPPDATA": share, "PATH": share}
	look := func(string) (string, error) { return share + `\Git\cmd\git.exe`, nil }
	for _, c := range bashCandidates(func(k string) string { return env[k] }, look) {
		if strings.Contains(c, "srv") {
			t.Errorf("a network share became a bash candidate: %q", c)
		}
	}
	if got := appendGitInstallRoots(nil, []string{share + `\Git`}); len(got) != 0 {
		t.Errorf("the registry source took a network root: %q", got)
	}
	pinned := share + `\bash.exe`
	launched := false
	if err := verifyShell("bash", pinned, func(string) bool { return true }, func(string) bool { launched = true; return true }, nil); err == nil || launched {
		t.Errorf("VerifyShell accepted a pinned network path (err=%v, launched=%v)", err, launched)
	}
	got := resolveShell("bash", pinned, nil, "windows", func(string) (string, error) { return "", filepath.ErrBadPattern },
		func(string) bool { return true }, nil, nil, func(string) bool { launched = true; return true }, func(string) bool { return false }, nil)
	if launched || got.Path == pinned {
		t.Errorf("a pinned network path was launched or chosen: %+v", got)
	}
}

// PATH lookups for bash and PowerShell are the highest-priority sources; a
// share on PATH is held to the same rule as every other.
func TestHostLookupsRefuseAShareOnPath(t *testing.T) {
	const share = `\\srv\share\bash.exe`
	launched := false
	h := shellHost{
		goos:     "windows",
		lookPath: func(string) (string, error) { return share, nil },
		exists:   func(string) bool { return true },
		probe:    func(string) bool { launched = true; return true },
		isWSL:    func(string) bool { return false },
		launches: func(string) bool { launched = true; return true },
	}
	if got := h.bashCandidates(); len(got) != 0 {
		t.Errorf("bashCandidates took a PATH share: %q", got)
	}
	if _, ok := h.powerShell([]string{"pwsh"}); ok {
		t.Error("powerShell took a PATH share")
	}
	if got := h.bashFallback(); got != FallbackNotFound {
		t.Errorf("bashFallback = %q, want not_found", got)
	}
	if launched {
		t.Error("a PATH share was launched")
	}
}

// Off Windows `//x` is an ordinary path.
func TestShellLaunchPlaceLeavesPosixPathsAlone(t *testing.T) {
	if !shellLaunchPlace(false, "//usr/bin/bash") || shellLaunchPlace(false, "") {
		t.Fatal("POSIX rules: //x is local, empty is not a place")
	}
}
