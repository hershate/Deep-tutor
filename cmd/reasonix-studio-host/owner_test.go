package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	goruntime "runtime"
	"strings"
	"testing"
)

// One act, one line. The parent reads this pipe a line at a time, so an act
// that shared a line with another would reach it as neither.
func TestEachActTravelsAsItsOwnLine(t *testing.T) {
	var out bytes.Buffer
	owner := &shellOwner{to: &out}

	if err := owner.PrepareForUpdate(context.Background()); err != nil {
		t.Fatalf("PrepareForUpdate: %v", err)
	}
	if out.Len() != 0 {
		t.Fatalf("releasing the application asked the shell for %q; it has nothing to do", out.String())
	}

	if err := owner.RelaunchAfterUpdate(context.Background()); err != nil {
		t.Fatalf("RelaunchAfterUpdate: %v", err)
	}
	owner.EndApplication(context.Background())

	lines := strings.Split(strings.TrimSuffix(out.String(), "\n"), "\n")
	// Linux replaced the files in place with nobody waiting to start what
	// replaced them, so the application arms its own restart. Elsewhere an
	// installer or a helper does, and arming would start a second one.
	want := []string{`{"act":"quit"}`}
	if goruntime.GOOS == "linux" {
		want = []string{`{"act":"relaunch"}`, `{"act":"quit"}`}
	}
	if len(lines) != len(want) {
		t.Fatalf("the shell was asked %v, want %v", lines, want)
	}
	for i := range want {
		if lines[i] != want[i] {
			t.Fatalf("act %d = %s, want %s", i, lines[i], want[i])
		}
	}
}

// A pipe that has gone is the parent having gone, which is not something to
// carry on past: the act is reported failed rather than assumed done.
func TestAnActThatCouldNotBeSentIsReported(t *testing.T) {
	owner := &shellOwner{to: brokenPipe{}}
	err := owner.ask(actQuit)
	if err == nil {
		t.Fatal("a write that failed was reported as an act delivered")
	}
	if !strings.Contains(err.Error(), actQuit) {
		t.Fatalf("the failure does not name the act: %v", err)
	}
}

type brokenPipe struct{}

func (brokenPipe) Write([]byte) (int, error) { return 0, context.Canceled }

// The capability is the gate, and it is a nil interface rather than a nil
// pointer inside one: a typed nil satisfies HubOptions.Update != nil, which
// would register the install routes for a host that cannot name the
// application it would be replacing, and fail nowhere.
func TestAHostThatCannotNameTheApplicationOwnsNoCapability(t *testing.T) {
	var out bytes.Buffer
	for _, tc := range []struct {
		name  string
		shell shellIdentity
	}{
		{"nothing stated", shellIdentity{}},
		{"a version but no application", shellIdentity{version: "v2.0.0"}},
		{"an application with no process", shellIdentity{version: "v2.0.0", exe: "/Applications/Reasonix Studio.app/Contents/MacOS/Reasonix Studio"}},
		{"a process but no application", shellIdentity{version: "v2.0.0", pid: 4321}},
		{"an unpackaged launch, which names no version", shellIdentity{exe: "/somewhere/studio", pid: 4321}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := studioUpdateHost(tc.shell, &shellOwner{to: &out}); got != nil {
				t.Fatalf("studioUpdateHost = %#v, want nil so no install route registers", got)
			}
		})
	}
}

// And the other direction, because a gate nothing passes is not a gate.
func TestAHostThatNamesTheApplicationOwnsOne(t *testing.T) {
	var out bytes.Buffer
	shell := shellIdentity{version: "v2.0.0", exe: "/Applications/Reasonix Studio.app/Contents/MacOS/Reasonix Studio", pid: 4321}
	if studioUpdateHost(shell, &shellOwner{to: &out}) == nil {
		t.Fatal("a host that stated its application got no capability")
	}
}

// The layout follows the executable the shell stated, never this process's.
// os.Executable() here names the host binary, which under this shell sits
// inside the very application an install would be replacing.
func TestTheInstallLayoutFollowsTheStatedApplication(t *testing.T) {
	stated := shellIdentity{version: "v2.0.0", exe: "/opt/studio/reasonix-studio", pid: 4321}
	install := studioInstall(stated)
	if install == nil {
		t.Fatal("a stated version got no install")
	}
	if install.Layout.Executable == "" || install.Layout.Root == "" {
		t.Fatalf("the layout is empty for a stated application: %+v", install.Layout)
	}
	if strings.Contains(install.Layout.Executable, "reasonix-studio-host") {
		t.Fatalf("the layout resolved this process rather than the application: %+v", install.Layout)
	}

	// A launch that named no version is not a Studio, and gets no install at
	// all rather than one with an empty version in it.
	if got := studioInstall(shellIdentity{exe: stated.exe, pid: stated.pid}); got != nil {
		t.Fatalf("studioInstall = %+v for a launch that named no version, want nil", got)
	}
}

// A clicked toast reaches the shell as one line on the pipe the handshake used,
// in the same shape the other acts take.
func TestAToastClickAsksTheShellToShowItsWindow(t *testing.T) {
	var out bytes.Buffer
	showOnToastClick(&shellOwner{to: &out}, io.Discard)()
	if got := out.String(); got != "{\"act\":\"show\"}\n" {
		t.Fatalf("pipe carried %q, want the show act", got)
	}
}

// The click is the first link of a chain that crosses two processes; when the
// window does not come forward, the log says whether the click arrived and
// whether the shell was reached.
func TestAToastClickIsWrittenToTheLog(t *testing.T) {
	var log bytes.Buffer
	showOnToastClick(&shellOwner{to: &bytes.Buffer{}}, &log)()
	if !strings.Contains(log.String(), "toast clicked") {
		t.Fatalf("log = %q, want the click recorded", log.String())
	}
	log.Reset()
	showOnToastClick(&shellOwner{to: failingWriter{}}, &log)()
	if !strings.Contains(log.String(), "could not reach the shell") {
		t.Fatalf("log = %q, want the failed ask recorded", log.String())
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("pipe closed") }
