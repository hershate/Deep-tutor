package plugin

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

type restartProbeTransport struct{ closed bool }

func (*restartProbeTransport) call(context.Context, string, any) (json.RawMessage, error) {
	return nil, nil
}
func (*restartProbeTransport) notify(context.Context, string, any) error { return nil }
func (p *restartProbeTransport) close()                                  { p.closed = true }

func TestRestartClosesTheOldChildBeforeDialingAndBoundsTheDialByTheCaller(t *testing.T) {
	old := &restartProbeTransport{}
	fresh := &restartProbeTransport{}
	var oldClosedAtDial bool
	var dialDeadline time.Time
	rt := newReconnectingTransport(context.Background(), old, time.Hour, func(_, callCtx context.Context) (transport, error) {
		oldClosedAtDial = old.closed
		dialDeadline, _ = callCtx.Deadline()
		return fresh, nil
	}, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := rt.restart(ctx); err != nil {
		t.Fatalf("restart: %v", err)
	}
	if !oldClosedAtDial {
		t.Fatal("the new child was started while the old one was still running")
	}
	if outer, _ := ctx.Deadline(); dialDeadline.IsZero() || dialDeadline.After(outer) {
		t.Fatalf("dial deadline %v outlives the caller's %v", dialDeadline, outer)
	}
	if got, _ := rt.current(); got != fresh {
		t.Fatal("the fresh child is not the active connection")
	}
}

func TestRestartDialFailureLeavesNoLiveConnection(t *testing.T) {
	old := &restartProbeTransport{}
	boom := errors.New("spawn failed")
	rt := newReconnectingTransport(context.Background(), old, time.Second, func(_, _ context.Context) (transport, error) { return nil, boom }, nil)
	if err := rt.restart(context.Background()); !errors.Is(err, boom) {
		t.Fatalf("restart = %v, want the dial error", err)
	}
	if !old.closed {
		t.Fatal("the old child was left running")
	}
	if _, err := rt.current(); err == nil {
		t.Fatal("a failed restart still reports a connection")
	}
	rt.close()
	if err := rt.restart(context.Background()); err == nil {
		t.Fatal("restart on a closed transport succeeded")
	}
}

// Only stdio children keep per-connection state that a rejected first message
// can ruin; an HTTP server is not restarted.
func TestInitializeOnFreshChildDoesNotRestartANonStdioTransport(t *testing.T) {
	dialed := false
	c := &Client{name: "web", transport: "http"}
	c.t = newReconnectingTransport(context.Background(), &restartProbeTransport{}, time.Second, func(_, _ context.Context) (transport, error) {
		dialed = true
		return &restartProbeTransport{}, nil
	}, nil)
	_ = c.initializeOnFreshChild(context.Background())
	if dialed {
		t.Fatal("an http transport was restarted after an unanswered probe")
	}
}
