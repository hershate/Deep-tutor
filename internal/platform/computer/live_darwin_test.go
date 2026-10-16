//go:build darwin

package computer

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
)

const targetBundle = "io.reasonix.test.computer-target"

// launchTarget builds the test application into a bundle and opens it, so it
// runs with an identity like any other application.
func launchTarget(t *testing.T) string {
	t.Helper()
	dir := testenv.TempDir(t)
	macos := filepath.Join(dir, "Target.app", "Contents", "MacOS")
	if err := os.MkdirAll(macos, 0o755); err != nil {
		t.Fatal(err)
	}
	plist := `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>` + targetBundle + `</string>
<key>CFBundleExecutable</key><string>target</string>
<key>CFBundleName</key><string>Computer Target</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>`
	if err := os.WriteFile(filepath.Join(dir, "Target.app", "Contents", "Info.plist"), []byte(plist), 0o644); err != nil {
		t.Fatal(err)
	}
	build := exec.Command("swiftc", "-O", "testdata/target.swift", "-o", filepath.Join(macos, "target"))
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build target: %v\n%s", err, out)
	}
	log := filepath.Join(dir, "target.log")
	if out, err := exec.Command("open", "-g", "-n", filepath.Join(dir, "Target.app"), "--args", log).CombinedOutput(); err != nil {
		t.Fatalf("open target: %v\n%s", err, out)
	}
	// Two instances of one bundle are two answers to "which application is
	// this", and the one that is on its way out captures nothing: the next test
	// waits out the screenshot timeout instead of failing for a reason.
	t.Cleanup(func() {
		_ = exec.Command("pkill", "-f", filepath.Join(macos, "target")).Run()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			if exec.Command("pgrep", "-f", filepath.Join(macos, "target")).Run() != nil {
				return
			}
			time.Sleep(50 * time.Millisecond)
		}
		t.Error("the target application was still running when the test ended")
	})
	waitLog(t, log, "ready")
	return log
}

func TestLiveOperatesAnApplicationWithoutItsPointer(t *testing.T) {
	s := liveSession(t)
	log := launchTarget(t)
	ctx, cancel := context.WithTimeout(t.Context(), 60*time.Second)
	defer cancel()
	snap, err := s.Snapshot(ctx, targetBundle)
	if err != nil {
		t.Fatal(err)
	}
	field := lineRef(t, snap.Lines, `"Probe field"`)
	result, err := s.Act(ctx, targetBundle, []Step{{Action: "set_value", Ref: field, Text: "from CUA"}})
	if (err != nil && CodeOf(err) != CodeEffectUnknown) || result.Done != 1 {
		t.Fatalf("set_value: %+v %v", result, err)
	}
	waitLog(t, log, "text from CUA")
	shot, _, err := s.Screenshot(ctx, targetBundle)
	if err != nil || !strings.HasPrefix(shot, "data:image/") {
		t.Fatalf("screenshot: %v", err)
	}
}
