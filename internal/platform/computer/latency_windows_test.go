package computer

import (
	"context"
	"testing"
	"time"
)

func TestLiveWindowsLatency(t *testing.T) {
	s := liveSession(t)
	_ = launchWindowsTarget(t)
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Minute)
	defer cancel()
	for round := range 3 {
		start := time.Now()
		apps, err := s.Apps(ctx)
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("round=%d apps=%d discovery=%s", round, len(apps), time.Since(start))
		start = time.Now()
		snap, err := s.Snapshot(ctx, windowsTarget)
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("round=%d snapshot=%s", round, time.Since(start))
		field := lineRef(t, snap.Lines, `"Probe field"`)
		start = time.Now()
		res, err := s.Act(ctx, windowsTarget, []Step{{Action: "set_value", Ref: field, Text: "latency probe"}})
		if (err != nil && CodeOf(err) != CodeEffectUnknown) || res.Done != 1 {
			t.Fatalf("%+v %v", res, err)
		}
		t.Logf("round=%d action=%s", round, time.Since(start))
		start = time.Now()
		if _, _, err := s.Screenshot(ctx, windowsTarget); err != nil {
			t.Fatal(err)
		}
		t.Logf("round=%d screenshot=%s", round, time.Since(start))
	}
}
