package serve

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"testing"
	"time"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/session/control"
)

// A retired pane must not leave its mirror of frames running. The subscription
// it holds keeps the broadcaster's queue alive, and the mirror waited for a
// channel that only closed when the mirror returned — so it never returned.
func TestRetiringAPaneReleasesItsWireLogSubscription(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	root := testenv.TempDir(t)
	ctrl := control.New(control.Options{WorkspaceRoot: root, SessionDir: SessionDirFor(root)})
	t.Cleanup(ctrl.Close)
	bc := NewBroadcaster()
	srv := New(ctrl, bc, config.ServeConfig{})
	h := NewHub(HubOptions{})
	rt, err := h.Adopt(srv, bc)
	if err != nil {
		t.Fatalf("adopt: %v", err)
	}
	if got := bc.Subscribers(); got != 1 {
		t.Fatalf("subscribers while the pane is open = %d, want 1", got)
	}
	if err := h.Close(rt.ID); err != nil {
		t.Fatalf("close: %v", err)
	}
	if got := bc.Subscribers(); got != 0 {
		t.Fatalf("subscribers after the pane was retired = %d, want 0", got)
	}
}

// The limiter must not need a goroutine to stay bounded. A server that cannot
// stop one leaks it, and a hub opens a gate for every pane it opens.
func TestRateLimitRemembersOnlySoManyAddresses(t *testing.T) {
	rl := newRateLimit()
	for i := range rateLimitTracked + 100 {
		if !rl.allow(fmt.Sprintf("10.%d.%d.%d", i/65536, (i/256)%256, i%256)) {
			t.Fatalf("a fresh address was refused: %d", i)
		}
	}
	if got := len(rl.attempts); got > rateLimitTracked {
		t.Fatalf("addresses remembered = %d, want at most %d", got, rateLimitTracked)
	}
}

// A launch that settles many questions must not keep every answer: the record
// exists so a retry can be told from a second opinion, and a retry follows
// within seconds.
func TestSettledQuestionsAreBounded(t *testing.T) {
	b := NewAskBroker(nil)
	for i := range askPastTracked + 50 {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err := b.Ask(ctx, Ask{Kind: fmt.Sprintf("probe-%d", i)}); err == nil {
			t.Fatalf("a cancelled question reported an answer: %d", i)
		}
	}
	if got := len(b.past); got > askPastTracked {
		t.Fatalf("settled questions remembered = %d, want at most %d", got, askPastTracked)
	}
}

// A share whose listener died must stop saying it is open. While it kept
// saying so, the panel showed "sharing" and the pairing code it handed out
// pointed at an address no phone could reach.
func TestAShareThatLostItsListenerSaysItIsClosed(t *testing.T) {
	share := NewDeviceShare(nil)
	share.addresses = func() []ShareAddress {
		return []ShareAddress{{Interface: "loopback", IP: "127.0.0.1", Kind: AddressLAN}}
	}
	share.persistPort = func(int) error { return nil }
	share.Attach(http.NotFoundHandler())
	st, err := share.Open("127.0.0.1")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if !st.Open {
		t.Fatal("the share did not report itself open")
	}
	share.mu.Lock()
	ln := share.live.ln
	share.mu.Unlock()
	if ln == nil {
		t.Fatal("the share kept no listener to lose")
	}
	// The listener dies the way a network change makes it die.
	if err := ln.Close(); err != nil {
		t.Fatalf("close the listener: %v", err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if !share.Status().Open {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if share.Status().Open {
		t.Fatal("a share whose listener died still reports itself open")
	}
	if _, err := share.Offer(); !errors.Is(err, ErrShareClosed) {
		t.Fatalf("offer after the listener died = %v, want ErrShareClosed", err)
	}
}

// A frame that was still being written when the log was read is not a frame
// yet. Handing it to a client as one would break its parse of the log.
func TestTheTrajectoryDropsAFrameThatWasStillBeingWritten(t *testing.T) {
	body := wholeWireLogLines([]byte("{\"kind\":\"turn_started\"}\n{\"kind\":\"turn_do"))
	if body != "{\"kind\":\"turn_started\"}\n" {
		t.Fatalf("log = %q, want the whole frame only", body)
	}
	if got := wholeWireLogLines([]byte("{\"kind\":\"turn_started\"}\n")); got != "{\"kind\":\"turn_started\"}\n" {
		t.Fatalf("log = %q, want it unchanged", got)
	}
}
