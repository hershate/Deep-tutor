//go:build windows

package scratchroot

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// fakeInfo is a directory that exists.
type fakeInfo struct{ name string }

func (f fakeInfo) Name() string       { return f.name }
func (f fakeInfo) Size() int64        { return 0 }
func (f fakeInfo) Mode() os.FileMode  { return os.ModeDir | 0o755 }
func (f fakeInfo) ModTime() time.Time { return time.Time{} }
func (f fakeInfo) IsDir() bool        { return true }
func (f fakeInfo) Sys() any           { return nil }

// volumeTemp answers for one volume's TEMP directory and defers the rest to
// the real filesystem.
func volumeTemp(name string, exists bool) func(string) (os.FileInfo, error) {
	return func(path string) (os.FileInfo, error) {
		if path == name {
			if !exists {
				return nil, os.ErrNotExist
			}
			return fakeInfo{name: filepath.Base(path)}, nil
		}
		return os.Stat(path)
	}
}

const systemTempDir = `C:\Users\Z\AppData\Local\Temp`

func TestVolumeTempOnTheProjectVolumeIsPreferred(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return `F:\app\deep-tutor.exe`, nil }
	notRedirected(systemTempDir)
	statPath = volumeTemp(`F:\TEMP`, true)
	probeWrites = func(string) bool { return true }

	want := filepath.Join(`F:\TEMP`, ProjectDirName)
	if got := resolve(); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}

func TestTheSystemVolumeIsNotAMove(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return `C:\app\deep-tutor.exe`, nil }
	notRedirected(systemTempDir)
	statPath = volumeTemp(`C:\TEMP`, true)
	probeWrites = func(string) bool { return true }

	want := filepath.Join(systemTempDir, ProjectDirName)
	if got := resolve(); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}

func TestAVolumeWithoutATempDirectoryFallsBack(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return `F:\app\deep-tutor.exe`, nil }
	notRedirected(systemTempDir)
	statPath = volumeTemp(`F:\TEMP`, false)
	probeWrites = func(string) bool { return true }

	want := filepath.Join(systemTempDir, ProjectDirName)
	if got := resolve(); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}

func TestAnUnwritableVolumeTempIsNotUsed(t *testing.T) {
	restoreSeams(t)
	envLookup = func(string) string { return "" }
	executable = func() (string, error) { return `F:\app\deep-tutor.exe`, nil }
	notRedirected(systemTempDir)
	statPath = volumeTemp(`F:\TEMP`, true)
	probeWrites = func(string) bool { return false }

	want := filepath.Join(systemTempDir, ProjectDirName)
	if got := resolve(); got != want {
		t.Fatalf("resolve() = %q, want %q", got, want)
	}
}
