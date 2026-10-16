package computer

import (
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/sys/windows"
)

func TestCUAWindowsPathIdentityUsesOSExecutableName(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	bundle, actual, err := processIdentity(int32(os.Getpid()), exe)
	if err != nil || bundle != filepath.Base(exe) || actual != filepath.Base(exe) {
		t.Fatalf("bundle=%q actual=%q err=%v", bundle, actual, err)
	}
	const aumid = "Example.Package!Application"
	bundle, _, err = processIdentity(int32(os.Getpid()), aumid)
	if err != nil || bundle != aumid {
		t.Fatalf("changed package identity: %q %v", bundle, err)
	}
}

// The catalogue's executable is resolved to its long form; a process started
// from a short (8.3) path reports the short one. Both name one file.
func TestCUAWindowsExecutableAttestationAcceptsShortAndLongSpellings(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	long, err := filepath.EvalSymlinks(exe)
	if err != nil {
		t.Fatal(err)
	}
	from, err := windows.UTF16PtrFromString(long)
	if err != nil {
		t.Fatal(err)
	}
	buf := make([]uint16, windows.MAX_PATH)
	n, err := windows.GetShortPathName(from, &buf[0], uint32(len(buf)))
	if err != nil || n == 0 || int(n) >= len(buf) {
		t.Skipf("no short name for %s: %v", long, err)
	}
	short := windows.UTF16ToString(buf[:n])
	if !sameExecutablePath(short, long) || !sameExecutablePath(long, short) {
		t.Fatalf("%q and %q name one file", short, long)
	}
	if sameExecutablePath(short, filepath.Join(filepath.Dir(long), "other.exe")) {
		t.Fatal("a different file was accepted")
	}
}
