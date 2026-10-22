// hub_capacity.go — how many panes one hub holds open at once.
//
// A pane is a whole runtime: a controller with its goroutines, a provider
// client, a session lease, and the MCP sidecars its workspace starts. Nothing
// said how many may exist at once, so a client reaching POST /runtimes could
// open panes until the process ran out of memory.
//
// The ceiling is checked before the expensive part: building a pane costs.
package serve

import (
	"fmt"
	"net/http"
)

// codeTooManyPanes is the refusal a client gets when the hub is already
// holding as many panes as it will hold.
const codeTooManyPanes = "hub.too_many_panes"

// paneLimit is the ceiling this hub holds panes to.
func (h *Hub) paneLimit() int {
	return h.opts.Serve.PaneLimit()
}

// paneClaim is a reserved place in the hub's ceiling: the pane it was taken
// for either becomes one, or gives the place back.
type paneClaim struct {
	hub   *Hub
	taken bool
}

// reservePane takes a place in the ceiling.
func (h *Hub) reservePane() (*paneClaim, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	limit := h.paneLimit()
	if len(h.order)+h.claims >= limit {
		return nil, h.paneRefusal(limit, len(h.order))
	}
	h.claims++
	return &paneClaim{hub: h}, nil
}

// published marks the place as taken by a pane.
func (c *paneClaim) published() { c.taken = true }

// giveBack returns the place when the pane never became one.
func (c *paneClaim) giveBack() {
	if c.taken {
		return
	}
	c.hub.releaseClaim()
}

// checkPaneRoom answers whether one more pane would fit, without reserving it.
//
// The remote path asks the other machine to open a runtime before this hub
// publishes one, so it has to know the answer before that call: a refusal
// afterwards would leave a pane open over there for nobody to close.
func (h *Hub) checkPaneRoom() error {
	h.mu.RLock()
	defer h.mu.RUnlock()
	limit := h.paneLimit()
	if len(h.order)+h.claims >= limit {
		return h.paneRefusal(limit, len(h.order))
	}
	return nil
}

// releaseClaim hands a reservation back when it never became a pane.
func (h *Hub) releaseClaim() {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.claims > 0 {
		h.claims--
	}
}

// paneRefusal says the hub is full. The limit travels as a parameter so a
// frontend can grey out the button at the right count instead of guessing.
func (h *Hub) paneRefusal(limit, open int) error {
	return refusal(http.StatusTooManyRequests, codeTooManyPanes,
		fmt.Errorf("this hub holds %d panes; close one before opening another", limit),
		map[string]any{"max": limit, "open": open})
}
