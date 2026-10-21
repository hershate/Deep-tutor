package plugin

import (
	"context"
	"errors"
	"testing"
	"time"
)

// A transport interface that carries a nil pointer is not itself nil. Every
// `!= nil` check downstream passes it on, and the first method call on it
// faults. The factory hands back exactly that on failure, so a server that
// cannot start must not take the process down with it.

func TestNewTransportReportsNoTransportOnFailure(t *testing.T) {
	got, err := newTransport(context.Background(), Spec{Name: "broken", Type: "stdio"})
	if err == nil {
		t.Fatal("newTransport accepted a spec with no command")
	}
	if got != nil {
		t.Fatalf("newTransport returned %T, want a nil interface", got)
	}
}

func TestClosingATypedNilTransportIsSafe(t *testing.T) {
	for name, transport := range map[string]transport{
		"stdio": (*stdioTransport)(nil),
		"http":  (*httpTransport)(nil),
		"sse":   (*sseTransport)(nil),
	} {
		transport.close()
		t.Logf("%s closed without faulting", name)
	}
}

func TestRestartDoesNotFaultOnATypedNilReplacement(t *testing.T) {
	old := &restartProbeTransport{}
	rt := newReconnectingTransport(context.Background(), old, time.Second,
		func(_, _ context.Context) (transport, error) {
			// What the factory returns when the server cannot start.
			return (*stdioTransport)(nil), errors.New("spawn failed")
		}, nil)

	if err := rt.restart(context.Background()); err == nil {
		t.Fatal("restart reported success for a dial that failed")
	}
	if _, err := rt.current(); err == nil {
		t.Fatal("a failed restart still reports a connection")
	}
}

func TestDialOneRejectsADialThatReportsSuccessWithoutATransport(t *testing.T) {
	rt := newReconnectingTransport(context.Background(), &restartProbeTransport{}, time.Second,
		func(_, _ context.Context) (transport, error) {
			return (*stdioTransport)(nil), nil
		}, nil)

	if _, err := rt.dialOne(); err == nil {
		t.Fatal("dialOne accepted a dial that produced no transport")
	}
}

func TestTransportIsNilRecognizesBothShapesOfAbsence(t *testing.T) {
	if !transportIsNil(nil) {
		t.Fatal("a nil interface is not absent")
	}
	if !transportIsNil((*stdioTransport)(nil)) {
		t.Fatal("a typed nil pointer is not absent")
	}
	if transportIsNil(&restartProbeTransport{}) {
		t.Fatal("a live transport reported as absent")
	}
}
