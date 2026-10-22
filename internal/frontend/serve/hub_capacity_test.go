package serve

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
)

// The ceiling is what keeps a client that can reach POST /runtimes from
// opening panes until the process runs out of memory: a pane is a whole
// runtime — a controller, a provider client, a session lease, the MCP sidecars
// its workspace starts.
func TestHubRefusesToOpenMorePanesThanTheCeiling(t *testing.T) {
	hubBootEnv(t)
	h := NewHub(HubOptions{Serve: config.ServeConfig{MaxPanes: 2}})
	defer h.Shutdown()
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()

	root := testenv.TempDir(t)
	for i := range 2 {
		if status, body := openPane(t, srv, root, ""); status != http.StatusOK {
			t.Fatalf("open %d: %d %s", i, status, body)
		}
	}
	status, body := openPane(t, srv, root, "")
	if status != http.StatusTooManyRequests || refusalCodeOf(t, body) != codeTooManyPanes {
		t.Fatalf("open past the ceiling: status=%d code=%q body=%s", status, refusalCodeOf(t, body), body)
	}
	if len(h.List()) != 2 {
		t.Fatalf("the refused open published a pane anyway: %d panes open", len(h.List()))
	}
}

// The refusal carries the limit so a frontend can grey the button out at the
// right count instead of guessing one.
func TestTheRefusalCarriesTheCeiling(t *testing.T) {
	hubBootEnv(t)
	h := NewHub(HubOptions{Serve: config.ServeConfig{MaxPanes: 1}})
	defer h.Shutdown()
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()

	root := testenv.TempDir(t)
	if status, body := openPane(t, srv, root, ""); status != http.StatusOK {
		t.Fatalf("first open: %d %s", status, body)
	}
	_, body := openPane(t, srv, root, "")
	if !strings.Contains(body, `"max":1`) {
		t.Fatalf("the refusal does not carry the ceiling: %s", body)
	}
}

// Closing a pane gives the room back: the ceiling counts what is open, not
// what was ever opened.
func TestClosingAPaneMakesRoomForAnother(t *testing.T) {
	hubBootEnv(t)
	h := NewHub(HubOptions{Serve: config.ServeConfig{MaxPanes: 1}})
	defer h.Shutdown()
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()

	root := testenv.TempDir(t)
	if status, body := openPane(t, srv, root, ""); status != http.StatusOK {
		t.Fatalf("first open: %d %s", status, body)
	}
	if status, _ := openPane(t, srv, root, ""); status != http.StatusTooManyRequests {
		t.Fatalf("open past the ceiling = %d, want 429", status)
	}
	if err := h.Close(h.List()[0].ID); err != nil {
		t.Fatalf("close: %v", err)
	}
	if status, body := openPane(t, srv, root, ""); status != http.StatusOK {
		t.Fatalf("open after closing: %d %s", status, body)
	}
}

// A pane that never became one must not keep its reservation: an open that
// fails after the reservation was taken would otherwise leak a slot per
// attempt, and the hub would fill up with panes that do not exist.
func TestAFailedOpenGivesTheRoomBack(t *testing.T) {
	hubBootEnv(t)
	h := NewHub(HubOptions{Serve: config.ServeConfig{MaxPanes: 1}})
	defer h.Shutdown()
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()

	gone := filepath.Join(testenv.TempDir(t), "no-such-folder")
	if status, _ := openPane(t, srv, gone, ""); status == http.StatusOK {
		t.Fatal("opening a folder that is not there succeeded")
	}
	root := testenv.TempDir(t)
	if status, body := openPane(t, srv, root, ""); status != http.StatusOK {
		t.Fatalf("open after a failed one: %d %s (the reservation leaked)", status, body)
	}
}

// Without a configured ceiling the hub holds the default number of panes.
func TestTheCeilingHasADefault(t *testing.T) {
	h := NewHub(HubOptions{})
	if got := h.paneLimit(); got != config.DefaultMaxPanes {
		t.Fatalf("pane limit = %d, want %d", got, config.DefaultMaxPanes)
	}
}
