//go:build windows

package computer

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/atotto/clipboard"
	"reasonix/internal/base/testenv"
)

const windowsTarget = "computer-target.exe"

// probe is the running test application and where it said its parts are, in
// screen pixels.
type probe struct {
	log            string
	button, canvas [2]float64
}

func launchWindowsTarget(t *testing.T) probe {
	t.Helper()
	dir := testenv.TempDir(t)
	exe := filepath.Join(dir, windowsTarget)
	if out, err := exec.Command("go", "build", "-o", exe, "./testdata/wintarget").CombinedOutput(); err != nil {
		t.Fatalf("build target: %v\n%s", err, out)
	}
	p := probe{log: filepath.Join(dir, "target.log")}
	cmd := exec.Command(exe, p.log)
	if err := cmd.Start(); err != nil {
		t.Fatalf("start target: %v", err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	})
	waitLog(t, p.log, "ready")
	f, err := os.Open(p.log)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var name string
		var x, y float64
		if n, _ := fmt.Sscanf(sc.Text(), "at %s %f %f", &name, &x, &y); n == 3 {
			switch name {
			case "button":
				p.button = [2]float64{x, y}
			case "canvas":
				p.canvas = [2]float64{x, y}
			}
		}
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return p
}

// inShot converts a point on screen into the pixels of the application's
// latest screenshot, which is the frame the model aims in.
func inShot(s *Session, at [2]float64) (*float64, *float64) {
	s.driver.desktop.Lock()
	g := s.state
	s.driver.desktop.Unlock()
	scale := g.window.Bounds.Width / float64(g.imageWidth)
	x, y := (at[0]-g.window.Bounds.X)/scale, (at[1]-g.window.Bounds.Y)/scale
	return &x, &y
}

func TestLiveWindowsOperatesAnApplication(t *testing.T) {
	s := liveSession(t)
	p := launchWindowsTarget(t)
	ctx, cancel := context.WithTimeout(t.Context(), 60*time.Second)
	defer cancel()
	snap, err := s.Snapshot(ctx, windowsTarget)
	if err != nil {
		t.Fatal(err)
	}
	field := lineRef(t, snap.Lines, `"Probe field"`)
	button := lineRef(t, snap.Lines, `"Probe button"`)
	result, err := s.Act(ctx, windowsTarget, []Step{{Action: "set_value", Ref: field, Text: "李雷 from CUA"}})
	if err != nil && CodeOf(err) != CodeEffectUnknown {
		t.Fatalf("set_value: %+v %v", result, err)
	}
	waitLog(t, p.log, "text 李雷 from CUA")
	result, err = s.Act(ctx, windowsTarget, []Step{{Action: "click", Ref: button}})
	if err != nil && CodeOf(err) != CodeEffectUnknown {
		t.Fatalf("click: %+v %v", result, err)
	}
	if err != nil && result.Done != 1 {
		t.Fatal("unverified click lost delivery fact")
	}
	waitLog(t, p.log, "button pressed 1")
	shot, app, err := s.Screenshot(ctx, windowsTarget)
	if err != nil || !strings.HasPrefix(shot, "data:image/") || app.Bundle != windowsTarget {
		t.Fatalf("screenshot: %+v %v", app, err)
	}
	x, y := inShot(s, p.button)
	if _, err := s.Act(ctx, windowsTarget, []Step{{Action: "click", X: x, Y: y}}); err != nil && CodeOf(err) != CodeEffectUnknown {
		t.Fatal(err)
	}
	waitLog(t, p.log, "button pressed 2")
	for _, name := range []string{"cmd.exe", "PowerShell.exe"} {
		if _, err := s.Snapshot(ctx, name); CodeOf(err) != CodeAppRefused {
			t.Fatal(err)
		}
	}
}

func TestLiveWindowsPasteGivesTheClipboardBack(t *testing.T) {
	s := liveSession(t)
	p := launchWindowsTarget(t)
	held, err := clipboard.ReadAll()
	if err != nil {
		t.Skipf("native clipboard observation unavailable: %v", err)
	}
	snap, err := s.Snapshot(t.Context(), windowsTarget)
	if err != nil {
		t.Fatal(err)
	}
	field := lineRef(t, snap.Lines, `"Probe field"`)
	result, err := s.Act(t.Context(), windowsTarget, []Step{{Action: "paste", Ref: field, Text: "粘贴 from CUA"}})
	if err != nil && CodeOf(err) != CodeEffectUnknown {
		t.Fatalf("paste: %+v %v", result, err)
	}
	waitLog(t, p.log, "text 粘贴 from CUA")
	back, err := clipboard.ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if back != held {
		t.Fatal("CUA text delivery changed clipboard")
	}
}

func TestLiveWindowsThePointerReachesWhatAccessibilityCannot(t *testing.T) {
	s := liveSession(t)
	p := launchWindowsTarget(t)
	if _, _, err := s.Screenshot(t.Context(), windowsTarget); err != nil {
		t.Fatal(err)
	}
	x, y := inShot(s, p.canvas)
	result, err := s.Act(t.Context(), windowsTarget, []Step{{Action: "pointer_click", X: x, Y: y}})
	if err != nil && CodeOf(err) != CodeEffectUnknown {
		t.Fatalf("pointer: %+v %v", result, err)
	}
	waitLog(t, p.log, "mouseDown")
}
