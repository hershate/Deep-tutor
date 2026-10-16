package boot

import (
	"context"
	"encoding/json"
	"path/filepath"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/session/control"
	"strings"
	"testing"
	"time"
)

type surfaceSteerProbe struct {
	base    *capabilityProbeProvider
	ready   chan struct{}
	release chan struct{}
}

func (p *surfaceSteerProbe) Name() string { return "surface-steer-probe" }
func (p *surfaceSteerProbe) Stream(ctx context.Context, req provider.Request) (<-chan provider.Chunk, error) {
	p.base.mu.Lock()
	round := p.base.round
	p.base.mu.Unlock()
	if round == 1 {
		close(p.ready)
		select {
		case <-p.release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return p.base.Stream(ctx, req)
}

func TestEffectSurfaceActionSteerDisplayAndProviderPayload(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	const spec = `{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true}},{"kind":"button","label":"Continue","actionId":"continue"}]}`
	base := &capabilityProbeProvider{calls: []string{chartCall(t, "tool:render_surface", spec), `{"action":"inspect","capability_id":"tool:render_surface"}`}}
	probe := &surfaceSteerProbe{base: base, ready: make(chan struct{}), release: make(chan struct{})}
	events := make(chan event.Event, 8)
	ctrl := buildChartAssembly(t, "surface-steer-effect", probe, dir, event.FuncSink(func(e event.Event) {
		if e.Kind == event.Steer {
			events <- e
		}
	}))
	defer ctrl.Close()
	ctrl.EnableInteractiveApproval()
	ctrl.SetSessionPath(filepath.Join(dir, ".reasonix", "sessions", "steer.jsonl"))
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- ctrl.Run(ctx, "show a draft and continue") }()
	select {
	case <-probe.ready:
	case err := <-done:
		t.Fatalf("ended early: %v", err)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	state, err := ctrl.SurfaceActionState("probe-0")
	if err != nil {
		t.Fatal(err)
	}
	action := state.Action
	action.ActionID = "continue"
	action.RequestID = "steer-request"
	action.Values = json.RawMessage(`{"draft":"  edited\n"}`)
	rec, err := ctrl.SubmitSurfaceAction(control.SurfaceActionRequest{Action: action, Delivery: "steer"}, nil)
	if err != nil || rec.Disposition != "steer_accepted" {
		t.Fatalf("steer: %+v %v", rec, err)
	}
	close(probe.release)
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	select {
	case e := <-events:
		if e.Text != "Continue" || e.ItemID != rec.ItemID {
			t.Fatalf("display: %+v", e)
		}
	default:
		t.Fatal("steer announcement absent")
	}
	base.mu.Lock()
	defer base.mu.Unlock()
	payload, _ := json.Marshal(action)
	found := false
	for _, req := range base.reqs {
		for _, m := range req.Messages {
			if strings.Contains(m.Content, string(payload)) {
				found = true
			}
		}
	}
	if !found {
		t.Fatal("structured steer payload absent from provider")
	}
	state, err = ctrl.SurfaceActionState("probe-0")
	if err != nil || state.CanWithdraw {
		t.Fatalf("consumed action is still withdrawable: %+v %v", state, err)
	}
}
