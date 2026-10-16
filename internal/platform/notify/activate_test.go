package notify

import (
	"errors"
	"testing"

	"reasonix/internal/contract/config"
)

func TestAClickReachesTheHandlerTheHostBound(t *testing.T) {
	var clicks int
	OnActivate(func() { clicks++ })
	activated()
	activated()
	if clicks != 2 {
		t.Fatalf("handler ran %d times for two clicks", clicks)
	}
	OnActivate(func() {})
	t.Cleanup(func() { OnActivate(func() {}) })
}

func TestAClickWithNoHandlerIsHarmless(t *testing.T) {
	onActivate.Store(nil)
	activated()
}

// Windows grants the right to take the foreground to the process a click
// activates, which is this one; the window that must come forward belongs to
// the shell, so the right is handed on before the click is forwarded.
func TestTheForegroundRightIsGrantedBeforeTheClickIsForwarded(t *testing.T) {
	var order []string
	prev := allowForeground
	allowForeground = func() { order = append(order, "grant") }
	OnActivate(func() { order = append(order, "forward") })
	t.Cleanup(func() { allowForeground = prev; OnActivate(func() {}) })
	activated()
	if len(order) != 2 || order[0] != "grant" || order[1] != "forward" {
		t.Fatalf("order = %v, want the grant first", order)
	}
}

// A toast that could not be delivered is a fact the host owns: the sender's
// error reaches whoever bound a handler instead of being discarded.
func TestAFailedSendIsReportedNotDiscarded(t *testing.T) {
	var got []error
	OnSendFailure(func(err error) { got = append(got, err) })
	t.Cleanup(func() { OnSendFailure(nil) })
	boom := errors.New("com push failed")
	announce(failingSender{boom}, config.NotificationsConfig{Enabled: true}, Message{Title: "t"})
	if len(got) != 1 || !errors.Is(got[0], boom) {
		t.Fatalf("reported = %v, want the sender's error", got)
	}
}

type failingSender struct{ err error }

func (f failingSender) Send(Message) error { return f.err }
